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
# MAGIC %md ## Reset demo state
# MAGIC Aligns the trip to the days we actually have a live forecast for (which
# MAGIC include the real poor-air-quality days), rebuilds a clean itinerary with
# MAGIC outdoor items so rescheduling has something to act on, and clears the
# MAGIC packing list — so every BEFORE/AFTER below is a clean, real delta.

# COMMAND ----------
_dest_id = tools.get_trip(TRIP_ID)["destination_id"]
_days = [r["forecast_date"] for r in lakebase.run_query(
    "SELECT DISTINCT forecast_date FROM weather_snapshots "
    "WHERE destination_id = %s ORDER BY forecast_date LIMIT 3", (_dest_id,))]
lakebase.run_write("UPDATE trips SET start_date = %s, end_date = %s WHERE trip_id = %s",
                   (str(_days[0]), str(_days[-1]), TRIP_ID))
lakebase.run_write("DELETE FROM itinerary_items WHERE trip_id = %s", (TRIP_ID,))

def _act(name):
    r = lakebase.run_query(
        "SELECT activity_id FROM activities WHERE destination_id = %s AND name = %s",
        (_dest_id, name))
    return r[0]["activity_id"] if r else None

_plan = [
    (_days[0], "09:00", "12:00", "Johnston Canyon Hike"),
    (_days[0], "13:00", "15:00", "Lake Louise Canoeing"),
    (_days[1], "09:00", "10:30", "Banff Park Museum"),
    (_days[1], "14:00", "16:00", "Banff Upper Hot Springs"),
    (_days[min(2, len(_days) - 1)], "09:00", "12:00", "Johnston Canyon Hike"),
]
for _d, _st, _et, _nm in _plan:
    tools.add_itinerary_item(TRIP_ID, str(_d), _nm, _act(_nm), _st, _et)

lakebase.run_write("DELETE FROM packing_items WHERE trip_id = %s", (TRIP_ID,))
print("Reset done. Trip now spans:", [str(d) for d in _days])

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
rows = tools.list_packing(TRIP_ID)
if rows:
    show("BEFORE — packing list:", rows)
else:
    print("BEFORE — packing list: (empty)")

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
