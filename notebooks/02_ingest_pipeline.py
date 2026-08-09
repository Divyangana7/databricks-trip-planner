# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Spark ingestion pipeline (evidence for "data pipeline in Spark")
# MAGIC
# MAGIC Open-Meteo / Wikimedia -> Delta **bronze** (raw) -> Spark transform ->
# MAGIC Delta **silver** (curated) -> upsert into Lakebase.
# MAGIC
# MAGIC Screenshot: (a) the before/after `weather_snapshots` count, (b) the pipeline
# MAGIC result dict, (c) the `weather_silver` Delta sample.

# COMMAND ----------
# MAGIC %pip install psycopg2-binary requests openai pgvector
# MAGIC %pip install sqlalchemy
# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
import os, sys
sys.path.insert(0, os.path.abspath(".."))   # repo root
import lakebase
from pipeline import ingest_spark

# COMMAND ----------
# MAGIC %md ## Before — current row counts
# COMMAND ----------
before = lakebase.run_query(
    "SELECT (SELECT COUNT(*) FROM weather_snapshots) AS weather, "
    "       (SELECT COUNT(*) FROM activities) AS activities"
)[0]
print("BEFORE:", before)

# COMMAND ----------
# MAGIC %md ## Run the pipeline (uses the notebook's `spark` session)
# COMMAND ----------
result = ingest_spark.run(spark=spark)
print("PIPELINE RESULT:", result)

# COMMAND ----------
# MAGIC %md ## After — row counts (should be higher)
# COMMAND ----------
after = lakebase.run_query(
    "SELECT (SELECT COUNT(*) FROM weather_snapshots) AS weather, "
    "       (SELECT COUNT(*) FROM activities) AS activities"
)[0]
print("AFTER:", after)

# COMMAND ----------
# MAGIC %md ## Delta silver sample (the Spark-curated layer)
# COMMAND ----------
import config
display(spark.table(f"{config.UC_CATALOG}.{config.UC_SCHEMA}.weather_silver").limit(15))
