# Databricks notebook source
# MAGIC %md
# MAGIC # 06 — Scale to 25 destinations (Spark-parallelized fetch)
# MAGIC
# MAGIC Bootstraps 25 outdoor destinations, then ingests weather for ALL of them
# MAGIC with the API calls **distributed across the cluster** (mapInPandas), not a
# MAGIC driver loop. Screenshot: the destination count by country, the parallel-fetch
# MAGIC result (partitions + rows), and the timing comparison.

# COMMAND ----------

# MAGIC %pip install psycopg2-binary pgvector openai requests
# MAGIC %pip install sqlalchemy

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys, time
sys.path.insert(0, os.path.abspath(".."))
import pandas as pd
import lakebase
from pipeline import destinations, ingest_spark, embed_job

# COMMAND ----------

# MAGIC %md ## 1. Bootstrap the destination catalog (geocode + describe + insert)

# COMMAND ----------

boot = destinations.bootstrap()
print("Bootstrap:", {k: boot[k] for k in ("inserted", "skipped", "total_destinations")})
if boot["failed"]:
    print("Failed:", boot["failed"])
display(pd.DataFrame(lakebase.run_query(
    "SELECT country, COUNT(*) AS destinations FROM destinations GROUP BY country ORDER BY destinations DESC")))

# COMMAND ----------

# MAGIC %md ## 2. Spark-parallelized weather fetch for ALL destinations

# COMMAND ----------

t0 = time.time()
res = ingest_spark.ingest_weather_spark(spark=spark, partitions=8)
res["seconds"] = round(time.time() - t0, 1)
print("PARALLEL FETCH:", res)

# COMMAND ----------

# MAGIC %md ## 3. Timing comparison — driver loop vs Spark (same destinations)
# MAGIC The Spark path spreads the fetch across partitions; the gap widens with more
# MAGIC destinations and more cluster cores.

# COMMAND ----------

dests = lakebase.run_query("SELECT destination_id, name, latitude, longitude FROM destinations")
t0 = time.time()
_ = ingest_spark._fetch_weather_rows(dests)          # driver: sequential
driver_secs = round(time.time() - t0, 1)
print(f"Driver loop (sequential):  {driver_secs}s for {len(dests)} destinations")
print(f"Spark parallel ({res['partitions']} partitions): {res['seconds']}s for {res['destinations']} destinations")

# COMMAND ----------

# MAGIC %md ## 4. Embed the new destinations, then a scale summary

# COMMAND ----------

print("Embedded destinations:", embed_job.embed_destinations())
summary = lakebase.run_query(
    """
    SELECT
      (SELECT COUNT(*) FROM destinations)                                   AS destinations,
      (SELECT COUNT(DISTINCT country) FROM destinations)                    AS countries,
      (SELECT COUNT(*) FROM weather_snapshots)                              AS weather_rows,
      (SELECT COUNT(*) FROM destinations WHERE description_embedding IS NOT NULL) AS destinations_embedded
    """)
display(pd.DataFrame(summary))