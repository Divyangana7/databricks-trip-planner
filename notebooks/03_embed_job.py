# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — Embedding job (evidence for "process unstructured data")
# MAGIC
# MAGIC Embeds destination descriptions, activity/attraction text, and user notes
# MAGIC (GTE-Large, 1024-dim) into the pgvector columns in Lakebase.
# MAGIC
# MAGIC Screenshot the **before** and **after** coverage tables: filled goes 0 -> total.

# COMMAND ----------
# MAGIC %pip install psycopg2-binary pgvector openai
# MAGIC %pip install sqlalchemy
# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
import os, sys
sys.path.insert(0, os.path.abspath(".."))
import pandas as pd
from pipeline import embed_job

# COMMAND ----------
# MAGIC %md ## Before — embedding coverage (expect filled = 0)
# COMMAND ----------
display(pd.DataFrame(embed_job.coverage()))

# COMMAND ----------
# MAGIC %md ## Run the embedding job
# COMMAND ----------
print("Embedded:", embed_job.run())

# COMMAND ----------
# MAGIC %md ## After — embedding coverage (expect filled = total)
# COMMAND ----------
display(pd.DataFrame(embed_job.coverage()))

# COMMAND ----------
# MAGIC %md ## Sanity check — a semantic search now works end-to-end
# COMMAND ----------
import lakebase, models
qvec = models.embed_text("rainy day indoor activity, art and history")
hits = lakebase.run_query(
    """
    SELECT name, category, is_outdoor,
           1 - (requirements_embedding <=> %s::vector) AS similarity
    FROM activities
    WHERE requirements_embedding IS NOT NULL
    ORDER BY requirements_embedding <=> %s::vector
    LIMIT 5
    """,
    (qvec, qvec),
)
display(pd.DataFrame(hits))
