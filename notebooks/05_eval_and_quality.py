# Databricks notebook source
# MAGIC %md
# MAGIC # 05 — Evaluation & data quality (the rigor layer)
# MAGIC
# MAGIC Three things most submissions skip:
# MAGIC 1. **Data-quality gate** — invariants asserted over Lakebase.
# MAGIC 2. **Retrieval recall** — measured HNSW vs exact top-k (the ivfflat→HNSW result).
# MAGIC 3. **Agent eval** — golden tasks scored by real outcomes, not by eyeballing text.
# MAGIC
# MAGIC Screenshot each output table + the summary lines.

# COMMAND ----------
# MAGIC %pip install psycopg2-binary pgvector openai
# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
import os, sys, json
sys.path.insert(0, os.path.abspath(".."))
import pandas as pd
from quality import checks
from eval import retrieval_eval, agent_eval

# COMMAND ----------
# MAGIC %md ## 1. Data-quality gate (all error-severity checks must pass)
# COMMAND ----------
report = checks.run_all()
print("GATE PASSED:", report["passed"], "| failed error checks:", report["n_failed_errors"])
display(pd.DataFrame(report["results"]))

# COMMAND ----------
# MAGIC %md ## 2. Retrieval recall @ 5 (index vs exact ground truth)
# MAGIC With HNSW this should be ~1.0 and return a full k results. Re-point the
# MAGIC index to ivfflat and re-run to reproduce the low-recall result.
# COMMAND ----------
rr = retrieval_eval.evaluate(k=5)
print("Index:", rr["index"], "| summary:", rr["summary"])
display(pd.DataFrame(rr["per_query"]))

# COMMAND ----------
# MAGIC %md ## 3. Agent golden-task eval (scored by real Lakebase outcomes)
# COMMAND ----------
ae = agent_eval.evaluate()
print("AGENT SCORE:", ae["score"], "| pass rate:", ae["pass_rate"])
display(pd.DataFrame(ae["results"]))
