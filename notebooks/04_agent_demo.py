# Databricks notebook source
# MAGIC %md
# MAGIC # 04 — Agent demo (evidence for "AI agent with read + write tools")
# MAGIC
# MAGIC Each section runs the agent and shows the Lakebase state BEFORE and AFTER,
# MAGIC proving the agent (a) retrieves semantically and (b) writes real changes.
# MAGIC Screenshot the before/after pairs and the agent's `reply` + `steps`.

# COMMAND ----------
# MAGIC %pip install psycopg2-binary pgvector openai
# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
import os, sys, json, datetime
sys.path.insert(0, os.path.abspath(".."))
import pandas as pd
import lakebase
from agent import agent, tools

# The demo trip: "Rockies Long Weekend" (Banff) — full itinerary + live forecast.
TRIP_ID = lakebase.run_query(
    "SELECT trip_id FROM trips WHERE title = 'Rockies Long Weekend'"
)[0]["trip_id"]
print("Demo trip_id:", TRIP_ID)

def show(title, rows):
    print(title)
    df = pd.DataFrame(rows)
    # Databricks display() can't render bare TIME/INTERVAL columns — stringify them.
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, (datetime.time, datetime.timedelta))).any():
            df[col] = df[col].astype(str)
    display(df)

# COMMAND ----------
# MAGIC %md ## 0. The weather the agent will reason about
# COMMAND ----------
show("Per-day weather (is_bad drives rescheduling):", tools.weather_by_day(TRIP_ID))

# COMMAND ----------
# MAGIC %md ## 1. RETRIEVE — ask the agent to find weather-appropriate activities
# COMMAND ----------
r = agent.run_agent("What indoor activities would suit a rainy or smoky day here?", trip_id=TRIP_ID)
print("REPLY:\n", r["reply"])
print("\nTOOL CALLS:", [s["tool"] for s in r["steps"]])

# COMMAND ----------
# MAGIC %md ## 2. WRITE — reschedule outdoor activities for the weather (BEFORE / AFTER)
# COMMAND ----------
show("BEFORE — itinerary:", tools.list_itinerary(TRIP_ID))

# COMMAND ----------
r = agent.run_agent(
    "Reschedule any outdoor activities that clash with the weather, and explain each change.",
    trip_id=TRIP_ID,
)
print("REPLY:\n", r["reply"])
print("\nTOOL CALLS:", json.dumps([s["tool"] for s in r["steps"]]))

# COMMAND ----------
show("AFTER — itinerary (note status='rescheduled' + reschedule_reason):",
     tools.list_itinerary(TRIP_ID))

# COMMAND ----------
# MAGIC %md ## 3. WRITE — build a packing list (BEFORE / AFTER)
# COMMAND ----------
show("BEFORE — packing list:", tools.list_packing(TRIP_ID))

# COMMAND ----------
r = agent.run_agent("Build me a packing list for this trip based on the weather and plan.",
                    trip_id=TRIP_ID)
print("REPLY:\n", r["reply"])
print("\nTOOL CALLS:", [s["tool"] for s in r["steps"]])

# COMMAND ----------
show("AFTER — packing list:", tools.list_packing(TRIP_ID))

# COMMAND ----------
# MAGIC %md ## 4. WRITE — add a single item on request (BEFORE / AFTER)
# COMMAND ----------
show("BEFORE — day 1 items:",
     [i for i in tools.list_itinerary(TRIP_ID) if str(i["day_date"]).endswith("-14")])

# COMMAND ----------
r = agent.run_agent("Add a relaxing hot springs stop on the afternoon of the first day.",
                    trip_id=TRIP_ID)
print("REPLY:\n", r["reply"])
print("\nTOOL CALLS:", [s["tool"] for s in r["steps"]])

# COMMAND ----------
show("AFTER — day 1 items:",
     [i for i in tools.list_itinerary(TRIP_ID) if str(i["day_date"]).endswith("-14")])
