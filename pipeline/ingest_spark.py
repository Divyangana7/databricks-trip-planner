"""
Spark ingestion pipeline.

Flow (this is the "data pipeline in Spark" requirement):
    Open-Meteo / Wikimedia  ->  Delta bronze (raw, append)  ->
    Spark transform (dedupe, type, derive)  ->  Delta silver (curated)  ->
    upsert into Lakebase (weather_snapshots, activities)

Why fetch in the driver then process in Spark: the destination set is small, so
we fetch once (avoids hammering the public APIs / 429s), land the raw payload in
a Delta bronze table as an immutable record, and do all transformation and the
lakehouse writes in Spark. The app and agent then read from Lakebase, never the
live APIs.
"""

import logging

from pyspark.sql import SparkSession
from pyspark.sql.functions import current_timestamp
from pyspark.sql.types import (DoubleType, LongType, StringType, StructField,
                               StructType)

import config
import lakebase
from clients import open_meteo, wikimedia

logger = logging.getLogger("trip-planner.pipeline")

_WEATHER_SCHEMA = StructType([
    StructField("destination_id", LongType()),
    StructField("forecast_ts", StringType()),
    StructField("forecast_date", StringType()),
    StructField("temperature_c", DoubleType()),
    StructField("precipitation_mm", DoubleType()),
    StructField("precipitation_prob", LongType()),
    StructField("wind_kph", DoubleType()),
    StructField("us_aqi", LongType()),
    StructField("pm2_5", DoubleType()),
    StructField("pm10", DoubleType()),
    StructField("uv_index", DoubleType()),
])

_ATTRACTION_SCHEMA = StructType([
    StructField("destination_id", LongType()),
    StructField("title", StringType()),
    StructField("description", StringType()),
    StructField("extract", StringType()),
    StructField("latitude", DoubleType()),
    StructField("longitude", DoubleType()),
    StructField("category", StringType()),
    StructField("is_outdoor", LongType()),   # 0/1 in Spark, cast to bool at upsert
])

_OUTDOOR_KW = ("park", "trail", "lake", "mountain", "garden", "falls", "canyon",
               "glacier", "summit", "valley", "river", "hot spring", "gondola",
               "viewpoint", "peak", "creek")
_INDOOR_KW = ("museum", "gallery", "hospital", "church", "cathedral", "library",
              "theatre", "theater", "station", "centre", "center")


def _spark(spark=None):
    return spark or SparkSession.builder.getOrCreate()


def _num(x):
    return float(x) if x is not None else None


def _int(x):
    return int(round(x)) if x is not None else None


# ----------------------------------------------------------------------------
# Weather
# ----------------------------------------------------------------------------
def _fetch_weather_rows(destinations: list[dict]) -> list[tuple]:
    rows = []
    for d in destinations:
        for s in open_meteo.weather_snapshots(d["latitude"], d["longitude"]):
            rows.append((
                int(d["destination_id"]),
                s["forecast_ts"],
                s.get("forecast_date") or s["forecast_ts"][:10],
                _num(s.get("temperature_c")),
                _num(s.get("precipitation_mm")),
                _int(s.get("precipitation_prob")),
                _num(s.get("wind_kph")),
                _int(s.get("us_aqi")),
                _num(s.get("pm2_5")),
                _num(s.get("pm10")),
                _num(s.get("uv_index")),
            ))
    return rows


def _upsert_weather_snapshots(rows: list[tuple]) -> int:
    if not rows:
        return 0
    sql = """
        INSERT INTO weather_snapshots
            (destination_id, forecast_ts, forecast_date, temperature_c,
             precipitation_mm, precipitation_prob, wind_kph, us_aqi, pm2_5,
             pm10, uv_index, source)
        VALUES %s
        ON CONFLICT (destination_id, forecast_ts) DO UPDATE SET
            temperature_c      = EXCLUDED.temperature_c,
            precipitation_mm   = EXCLUDED.precipitation_mm,
            precipitation_prob = EXCLUDED.precipitation_prob,
            wind_kph           = EXCLUDED.wind_kph,
            us_aqi             = EXCLUDED.us_aqi,
            pm2_5              = EXCLUDED.pm2_5,
            pm10               = EXCLUDED.pm10,
            uv_index           = EXCLUDED.uv_index,
            captured_at        = now()
    """
    template = "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open-meteo')"
    return lakebase.run_values(sql, rows, template=template)


def ingest_weather(spark=None, catalog=None, schema=None) -> dict:
    """Fetch forecasts, land bronze+silver Delta, upsert Lakebase weather_snapshots."""
    spark = _spark(spark)
    catalog = catalog or config.UC_CATALOG
    schema = schema or config.UC_SCHEMA

    dests = lakebase.run_query(
        "SELECT destination_id, name, latitude, longitude, timezone FROM destinations"
    )
    raw = _fetch_weather_rows(dests)
    if not raw:
        return {"fetched": 0}

    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
    sdf = spark.createDataFrame(raw, schema=_WEATHER_SCHEMA)

    # Bronze: immutable raw history (append).
    (sdf.withColumn("_ingested_at", current_timestamp())
        .write.format("delta").mode("append")
        .saveAsTable(f"{catalog}.{schema}.weather_bronze"))

    # Silver: one row per (destination, hour), curated (overwrite current view).
    silver = sdf.dropDuplicates(["destination_id", "forecast_ts"])
    (silver.write.format("delta").mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(f"{catalog}.{schema}.weather_silver"))

    # Sync curated rows into Lakebase for the app/agent to read.
    tuples = [
        (r["destination_id"], r["forecast_ts"], r["forecast_date"],
         r["temperature_c"], r["precipitation_mm"], r["precipitation_prob"],
         r["wind_kph"], r["us_aqi"], r["pm2_5"], r["pm10"], r["uv_index"])
        for r in silver.collect()
    ]
    upserted = _upsert_weather_snapshots(tuples)
    return {"fetched": len(raw), "silver_rows": silver.count(),
            "lakebase_upserted": upserted,
            "bronze_table": f"{catalog}.{schema}.weather_bronze",
            "silver_table": f"{catalog}.{schema}.weather_silver"}


# ----------------------------------------------------------------------------
# Attractions -> activities (unstructured text that later gets embedded)
# ----------------------------------------------------------------------------
def _classify(title: str, extract: str | None) -> tuple[str, int]:
    t = f"{title} {extract or ''}".lower()
    if any(k in t for k in _INDOOR_KW):
        return "attraction", 0
    if any(k in t for k in _OUTDOOR_KW):
        return "outdoor", 1
    return "attraction", 0


def _fetch_attraction_rows(destinations: list[dict], radius_m: int, limit: int) -> list[tuple]:
    rows = []
    for d in destinations:
        for a in wikimedia.nearby_attractions(d["latitude"], d["longitude"], radius_m, limit):
            category, is_outdoor = _classify(a.get("title", ""), a.get("extract"))
            rows.append((
                int(d["destination_id"]),
                a.get("title"),
                a.get("description"),
                a.get("extract"),
                _num(a.get("latitude")),
                _num(a.get("longitude")),
                category,
                is_outdoor,
            ))
    return rows


def _upsert_activities(spark_rows) -> int:
    """Insert new attraction-activities, skipping names that already exist per destination."""
    inserted = 0
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            for r in spark_rows:
                cur.execute(
                    """
                    INSERT INTO activities
                        (destination_id, name, category, description, requirements,
                         is_outdoor, weather_sensitive, latitude, longitude, source)
                    SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, 'wikimedia'
                    WHERE NOT EXISTS (
                        SELECT 1 FROM activities
                        WHERE destination_id = %s AND name = %s
                    )
                    """,
                    (r["destination_id"], r["title"], r["category"], r["extract"],
                     r["extract"], bool(r["is_outdoor"]), bool(r["is_outdoor"]),
                     r["latitude"], r["longitude"],
                     r["destination_id"], r["title"]),
                )
                inserted += cur.rowcount
        conn.commit()
    return inserted


def ingest_attractions(spark=None, catalog=None, schema=None,
                       radius_m: int = 10000, limit: int = 8) -> dict:
    """Fetch nearby attractions, land bronze Delta, add new ones to Lakebase activities."""
    spark = _spark(spark)
    catalog = catalog or config.UC_CATALOG
    schema = schema or config.UC_SCHEMA

    dests = lakebase.run_query(
        "SELECT destination_id, name, latitude, longitude FROM destinations"
    )
    raw = _fetch_attraction_rows(dests, radius_m, limit)
    if not raw:
        return {"fetched": 0}

    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
    sdf = spark.createDataFrame(raw, schema=_ATTRACTION_SCHEMA)
    (sdf.withColumn("_ingested_at", current_timestamp())
        .write.format("delta").mode("append")
        .saveAsTable(f"{catalog}.{schema}.attractions_bronze"))

    inserted = _upsert_activities(sdf.dropDuplicates(["destination_id", "title"]).collect())
    return {"fetched": len(raw), "activities_inserted": inserted,
            "bronze_table": f"{catalog}.{schema}.attractions_bronze"}


def run(spark=None, catalog=None, schema=None) -> dict:
    """Run the full ingestion pipeline."""
    w = ingest_weather(spark, catalog, schema)
    a = ingest_attractions(spark, catalog, schema)
    logger.info("weather=%s attractions=%s", w, a)
    return {"weather": w, "attractions": a}
