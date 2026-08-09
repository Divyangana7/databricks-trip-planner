# Databricks notebook source
# MAGIC %md
# MAGIC # 00 — Set up Lakebase (schema → seed → verify)
# MAGIC
# MAGIC Runs `sql/schema.sql`, `sql/seed.sql`, and `sql/verify.sql` against Lakebase
# MAGIC using the **native-password role** in the `database/lakebase-url` secret — the
# MAGIC same role the app uses, so table ownership is consistent (no GRANTs needed).
# MAGIC
# MAGIC **Prerequisites**
# MAGIC 1. Lakebase instance + native-password role created; URL stored via `setup_secrets.py`.
# MAGIC 2. This notebook lives inside the repo (Git folder), so `../sql/*.sql` resolves.
# MAGIC    If not, set `SQL_DIR` below to the absolute path of the repo's `sql/` folder.

# COMMAND ----------

# MAGIC %pip install psycopg2-binary
# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
import os
import psycopg2

# The stored secret returns the plain connection URL (redacted in output).
LAKEBASE_URL = dbutils.secrets.get(scope="database", key="lakebase-url")

# Where the .sql files live. Default assumes this notebook is in <repo>/notebooks/.
SQL_DIR = os.environ.get("SQL_DIR", "../sql")

def run_sql_file(path: str):
    """Execute an entire .sql file (multi-statement) with autocommit on."""
    with open(path, "r") as f:
        sql = f.read()
    conn = psycopg2.connect(LAKEBASE_URL)
    conn.autocommit = True  # needed for CREATE EXTENSION / CREATE INDEX
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        print(f"OK: {path}")
    finally:
        conn.close()

# COMMAND ----------
# MAGIC %md ## 1. Create the schema (7 tables + pgvector)
# COMMAND ----------
run_sql_file(f"{SQL_DIR}/schema.sql")

# COMMAND ----------
# MAGIC %md ## 2. Seed the tables
# COMMAND ----------
run_sql_file(f"{SQL_DIR}/seed.sql")

# COMMAND ----------
# MAGIC %md ## 3. Verify — screenshot THIS table for your submission
# COMMAND ----------
import pandas as pd

conn = psycopg2.connect(LAKEBASE_URL)
try:
    counts_sql = """
    SELECT 'users' AS table_name, COUNT(*) AS rows, 2 AS minimum, (COUNT(*) >= 2) AS pass FROM users
    UNION ALL SELECT 'destinations',      COUNT(*), 3,  (COUNT(*) >= 3)  FROM destinations
    UNION ALL SELECT 'trips',             COUNT(*), 3,  (COUNT(*) >= 3)  FROM trips
    UNION ALL SELECT 'activities',        COUNT(*), 12, (COUNT(*) >= 12) FROM activities
    UNION ALL SELECT 'itinerary_items',   COUNT(*), 9,  (COUNT(*) >= 9)  FROM itinerary_items
    UNION ALL SELECT 'weather_snapshots', COUNT(*), 6,  (COUNT(*) >= 6)  FROM weather_snapshots
    UNION ALL SELECT 'packing_items',     COUNT(*), 6,  (COUNT(*) >= 6)  FROM packing_items
    ORDER BY table_name;
    """
    df = pd.read_sql(counts_sql, conn)
finally:
    conn.close()

print("All rows should show pass = True:")
display(df)
