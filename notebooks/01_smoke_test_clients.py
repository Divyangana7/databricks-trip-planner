# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Smoke test the clients (evidence for "third-party API" + AI layer)
# MAGIC
# MAGIC Runs one live call against each integration and shows the result. Screenshot
# MAGIC the outputs — together they prove Open-Meteo, Wikimedia, and the Foundation
# MAGIC Model APIs all work before we wire them into the pipeline and agent.
# MAGIC
# MAGIC Assumes this notebook is in `<repo>/notebooks/`. If your repo root isn't on
# MAGIC the path, set `REPO_ROOT` below and uncomment the sys.path line.

# COMMAND ----------
# MAGIC %pip install requests openai sqlalchemy
# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
import os, sys
# REPO_ROOT = "/Workspace/Users/you@example.com/databricks-trip-planner"
# sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.abspath(".."))   # repo root when run from notebooks/

from clients import open_meteo, wikimedia
import models

# COMMAND ----------
# MAGIC %md ## 1. Open-Meteo — geocoding + weather + air quality
# COMMAND ----------
place = open_meteo.geocode("Banff", count=1)[0]
print("Geocoded:", place)

snaps = open_meteo.weather_snapshots(place["latitude"], place["longitude"])
print(f"Fetched {len(snaps)} hourly snapshots. Sample:")
import pandas as pd
display(pd.DataFrame(snaps).head(12))

# COMMAND ----------
# MAGIC %md ## 2. Wikimedia — description + nearby attractions
# COMMAND ----------
print("Summary:", wikimedia.summary("Banff, Alberta")["description"])
near = wikimedia.nearby_attractions(place["latitude"], place["longitude"], radius_m=8000, limit=5)
_df = pd.DataFrame(near)
_cols = [c for c in ["title", "distance_m", "description"] if c in _df.columns]
display(_df[_cols])

# COMMAND ----------
# MAGIC %md ## 3. Foundation Model APIs — embeddings + chat
# COMMAND ----------
vec = models.embed_text("A weather-aware itinerary for a mountain hiking trip.")
print(f"Embedding dimension = {len(vec)} (expected 1024)")

reply = models.chat(
    [{"role": "user", "content": "In one sentence, why reschedule an outdoor hike when rain is forecast?"}],
    max_tokens=60,
)
print("Chat reply:", reply.choices[0].message.content)
