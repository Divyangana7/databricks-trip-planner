"""
Agent evaluation harness.

Runs a set of "golden tasks" through the agent and scores each by a programmatic
assertion against real Lakebase state (not by eyeballing the text). Each task:
  * sets up a known state,
  * sends a prompt to the agent,
  * checks an outcome (a tool fired, a row changed, a constraint holds).

This is the differentiator most submissions skip: the agent is *measured*, not
just built. Returns a pass/fail table + an overall score.
"""

import logging

import lakebase
import models
from agent import agent, tools

logger = logging.getLogger("trip-planner.eval.agent")


def _demo_trip_id():
    return lakebase.run_query(
        "SELECT trip_id FROM trips WHERE title = 'Rockies Long Weekend'")[0]["trip_id"]


def _reset_to_bad_weather(trip_id):
    """Align trip to forecast days + put outdoor items on them (so reschedule has work)."""
    dest_id = tools.get_trip(trip_id)["destination_id"]
    days = [r["forecast_date"] for r in lakebase.run_query(
        "SELECT DISTINCT forecast_date FROM weather_snapshots WHERE destination_id=%s "
        "ORDER BY forecast_date LIMIT 3", (dest_id,))]
    lakebase.run_write("UPDATE trips SET start_date=%s, end_date=%s WHERE trip_id=%s",
                       (str(days[0]), str(days[-1]), trip_id))
    lakebase.run_write("DELETE FROM itinerary_items WHERE trip_id=%s", (trip_id,))
    for d, nm in [(days[0], "Johnston Canyon Hike"), (days[0], "Lake Louise Canoeing"),
                  (days[1], "Banff Park Museum")]:
        a = lakebase.run_query(
            "SELECT activity_id FROM activities WHERE destination_id=%s AND name=%s", (dest_id, nm))
        tools.add_itinerary_item(trip_id, str(d), nm, a[0]["activity_id"] if a else None)
    lakebase.run_write("DELETE FROM packing_items WHERE trip_id=%s", (trip_id,))
    return days


# --- individual golden tasks (each returns (passed, detail)) ----------------
def task_retrieval_indoor(trip_id):
    r = agent.run_agent("Find me indoor activities for a smoky day.", trip_id=trip_id)
    used_search = any(s["tool"] == "search_activities" for s in r["steps"])
    return used_search, f"tools={[s['tool'] for s in r['steps']]}"


def task_reschedule_moves_outdoor(trip_id):
    _reset_to_bad_weather(trip_id)
    before = tools.list_itinerary(trip_id)
    outdoor_before = [i for i in before if i["is_outdoor"] and i["status"] == "planned"]
    agent.run_agent("Reschedule outdoor activities that clash with the weather.", trip_id=trip_id)
    after = tools.list_itinerary(trip_id)
    still_bad = [i for i in after if i["is_outdoor"] and i["status"] == "planned"
                 and i["day_date"] in {d["forecast_date"] for d in tools.weather_by_day(trip_id) if d["is_bad"]}]
    passed = len(outdoor_before) > 0 and len(still_bad) == 0
    return passed, f"outdoor_before={len(outdoor_before)} still_on_bad_day_after={len(still_bad)}"


def task_reschedule_records_reason(trip_id):
    changed = [i for i in tools.list_itinerary(trip_id) if i["status"] == "rescheduled"]
    have_reasons = [i for i in changed if i["reschedule_reason"]]
    return len(changed) > 0 and len(have_reasons) == len(changed), \
        f"rescheduled={len(changed)} with_reason={len(have_reasons)}"


def task_packing_created(trip_id):
    lakebase.run_write("DELETE FROM packing_items WHERE trip_id=%s", (trip_id,))
    before = len(tools.list_packing(trip_id))
    agent.run_agent("Build a packing list based on the weather.", trip_id=trip_id)
    after = len(tools.list_packing(trip_id))
    return after > before, f"before={before} after={after}"


def task_add_specific_item(trip_id):
    before = len(tools.list_itinerary(trip_id))
    agent.run_agent("Add a coffee stop tomorrow morning.", trip_id=trip_id)
    after = len(tools.list_itinerary(trip_id))
    return after == before + 1, f"before={before} after={after}"


def task_weather_grounded(trip_id):
    r = agent.run_agent("Is the air quality a concern for this trip?", trip_id=trip_id)
    used_weather = any(s["tool"] == "weather_by_day" for s in r["steps"])
    return used_weather, f"tools={[s['tool'] for s in r['steps']]}"


TASKS = [
    ("retrieval: searches for indoor activities", task_retrieval_indoor),
    ("reschedule: no outdoor item left on a bad day", task_reschedule_moves_outdoor),
    ("reschedule: every change has a reason", task_reschedule_records_reason),
    ("packing: list is created", task_packing_created),
    ("add: exactly one item added", task_add_specific_item),
    ("grounding: checks weather before answering", task_weather_grounded),
]


def evaluate() -> dict:
    trip_id = _demo_trip_id()
    results = []
    for name, fn in TASKS:
        try:
            passed, detail = fn(trip_id)
        except Exception as exc:  # a crash is a failed task, not a crashed eval
            passed, detail = False, f"ERROR: {type(exc).__name__}: {exc}"
        results.append({"task": name, "passed": bool(passed), "detail": detail})
    n_pass = sum(r["passed"] for r in results)
    return {"score": f"{n_pass}/{len(results)}", "pass_rate": round(n_pass / len(results), 3),
            "results": results}
