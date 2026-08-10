"""
Spark ingestion pipeline.

Flow (the "data pipeline in Spark" requirement):
    Open-Meteo / Wikimedia  ->  Delta bronze (raw, append)  ->
    Spark transform (dedupe, type, derive)  ->  Delta silver (curated)  ->
    upsert into Lakebase (weather_snapshots, activities)

Delta target resolution:
  * Uses config.UC_CATALOG / UC_SCHEMA if set, else the session's CURRENT
    catalog/schema (which you usually own).
  * Delta writes are BEST-EFFORT: if you lack permission, the pipeline logs a
    warning, skips the managed-table write, and still runs the Spark transform +
    Lakebase upsert. Set WRITE_DELTA=false to skip Delta entirely.
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
    StructField("is_outdoor", LongType()),
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


def _resolve_target(spark, catalog, schema):
    """Pick a Delta target: explicit args > config > session current catalog/schema."""
    catalog = catalog or getattr(config, "UC_CATALOG", "") or spark.catalog.currentCatalog()
    schema = schema or getattr(config, "UC_SCHEMA", "") or spark.catalog.currentDatabase()
    return catalog, schema


def _q(identifier: str) -> str:
    """Backtick-quote an identifier so names with hyphens/spaces work in Spark SQL.

    Normalizes any backticks the caller already added, so both "divy-catalog"
    and "`divy-catalog`" resolve to the same quoted form.
    """
    name = identifier.strip().strip("`").replace("`", "``")
    return f"`{name}`"


def _write_delta(spark, df, catalog, schema, table, mode):
    """Best-effort managed Delta write. Returns the table name, or None if skipped/denied."""
    if not getattr(config, "WRITE_DELTA", True):
        logger.info("WRITE_DELTA=false; skipping Delta write for %s", table)
        return None
    schema_fqn = f"{_q(catalog)}.{_q(schema)}"
    fq = f"{schema_fqn}.{_q(table)}"
    try:
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {schema_fqn}")
    except Exception as exc:
        logger.warning("No CREATE SCHEMA on %s (%s); will try writing if it already exists.",
                       schema_fqn, exc)
    writer = df.write.format("delta").mode(mode)
    if mode == "overwrite":
        writer = writer.option("overwriteSchema", "true")
    try:
        writer.saveAsTable(fq)
        logger.info("Wrote Delta table %s", fq)
        return fq
    except Exception as exc:
        logger.warning("Delta write to %s skipped (%s). Pipeline continues with Lakebase only.", fq, exc)
        return None


# ----------------------------------------------------------------------------
# Weather
# ----------------------------------------------------------------------------
def _fetch_weather_rows(destinations):
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


def _upsert_weather_snapshots(rows):
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
    lakebase.run_values(sql, rows, template=template)
    # Every row is inserted-or-updated (ON CONFLICT DO UPDATE), so the effective
    # upsert count is len(rows). We don't use run_values' return here because
    # execute_values pages the insert and cur.rowcount reflects only the last page.
    return len(rows)


def ingest_weather(spark=None, catalog=None, schema=None):
    """Fetch forecasts, land bronze+silver Delta (best-effort), upsert Lakebase."""
    spark = _spark(spark)
    catalog, schema = _resolve_target(spark, catalog, schema)

    dests = lakebase.run_query(
        "SELECT destination_id, name, latitude, longitude, timezone FROM destinations"
    )
    raw = _fetch_weather_rows(dests)
    if not raw:
        return {"fetched": 0}

    sdf = spark.createDataFrame(raw, schema=_WEATHER_SCHEMA)
    bronze = _write_delta(spark, sdf.withColumn("_ingested_at", current_timestamp()),
                          catalog, schema, "weather_bronze", "append")

    silver_df = sdf.dropDuplicates(["destination_id", "forecast_ts"])
    silver = _write_delta(spark, silver_df, catalog, schema, "weather_silver", "overwrite")

    tuples = [
        (r["destination_id"], r["forecast_ts"], r["forecast_date"],
         r["temperature_c"], r["precipitation_mm"], r["precipitation_prob"],
         r["wind_kph"], r["us_aqi"], r["pm2_5"], r["pm10"], r["uv_index"])
        for r in silver_df.collect()
    ]
    upserted = _upsert_weather_snapshots(tuples)
    return {"fetched": len(raw), "silver_rows": silver_df.count(),
            "lakebase_upserted": upserted, "bronze_table": bronze,
            "silver_table": silver, "target": f"{catalog}.{schema}"}


# ----------------------------------------------------------------------------
# Attractions -> activities
# ----------------------------------------------------------------------------
def _classify(title, extract):
    t = f"{title} {extract or ''}".lower()
    if any(k in t for k in _INDOOR_KW):
        return "attraction", 0
    if any(k in t for k in _OUTDOOR_KW):
        return "outdoor", 1
    return "attraction", 0


def _fetch_attraction_rows(destinations, radius_m, limit):
    rows = []
    for d in destinations:
        for a in wikimedia.nearby_attractions(d["latitude"], d["longitude"], radius_m, limit):
            category, is_outdoor = _classify(a.get("title", ""), a.get("extract"))
            rows.append((
                int(d["destination_id"]), a.get("title"), a.get("description"),
                a.get("extract"), _num(a.get("latitude")), _num(a.get("longitude")),
                category, is_outdoor,
            ))
    return rows


def _upsert_activities(spark_rows):
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
                     r["latitude"], r["longitude"], r["destination_id"], r["title"]),
                )
                inserted += cur.rowcount
        conn.commit()
    return inserted


def ingest_attractions(spark=None, catalog=None, schema=None, radius_m=10000, limit=8):
    """Fetch nearby attractions, land bronze Delta (best-effort), add new activities."""
    spark = _spark(spark)
    catalog, schema = _resolve_target(spark, catalog, schema)

    dests = lakebase.run_query(
        "SELECT destination_id, name, latitude, longitude FROM destinations"
    )
    raw = _fetch_attraction_rows(dests, radius_m, limit)
    if not raw:
        return {"fetched": 0}

    sdf = spark.createDataFrame(raw, schema=_ATTRACTION_SCHEMA)
    bronze = _write_delta(spark, sdf.withColumn("_ingested_at", current_timestamp()),
                          catalog, schema, "attractions_bronze", "append")

    inserted = _upsert_activities(sdf.dropDuplicates(["destination_id", "title"]).collect())
    return {"fetched": len(raw), "activities_inserted": inserted, "bronze_table": bronze}


def run(spark=None, catalog=None, schema=None, gate: bool = False):
    w = ingest_weather(spark, catalog, schema)
    a = ingest_attractions(spark, catalog, schema)
    logger.info("weather=%s attractions=%s", w, a)
    result = {"weather": w, "attractions": a}
    if gate:
        # Fail loud if the freshly-ingested data violates an invariant.
        from quality import checks
        result["quality"] = checks.gate()
    return result
