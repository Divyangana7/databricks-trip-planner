"""
Agent tools.

Read tools (retrieve): get_trip, weather_by_day, list_itinerary, search_activities.
Write tools (real actions against Lakebase): add/move/remove itinerary items,
add packing items.
Composite tools (deterministic, weather-aware): generate_itinerary,
reschedule_outdoor_for_weather, build_packing_list.

Every write returns the row(s) it changed (via RETURNING) so an action can be
proven immediately.
"""

import logging
from datetime import timedelta

import config
import lakebase
import models

logger = logging.getLogger("trip-planner.tools")


# ---------------------------------------------------------------- reads -----
def get_trip(trip_id: int) -> dict:
    rows = lakebase.run_query(
        """
        SELECT t.trip_id, t.title, t.start_date, t.end_date, t.status,
               d.destination_id, d.name AS destination, d.country
        FROM trips t
        JOIN destinations d ON d.destination_id = t.destination_id
        WHERE t.trip_id = %s
        """,
        (trip_id,),
    )
    return rows[0] if rows else {"error": f"trip {trip_id} not found"}


def list_itinerary(trip_id: int) -> list[dict]:
    return lakebase.run_query(
        """
        SELECT i.item_id, i.day_date, i.start_time, i.end_time, i.title,
               i.status, i.reschedule_reason, i.activity_id,
               COALESCE(a.is_outdoor, FALSE) AS is_outdoor, a.category
        FROM itinerary_items i
        LEFT JOIN activities a ON a.activity_id = i.activity_id
        WHERE i.trip_id = %s
        ORDER BY i.day_date, i.sort_order
        """,
        (trip_id,),
    )


def weather_by_day(trip_id: int) -> list[dict] | dict:
    """Per-day weather summary for the trip's destination, with a bad-day flag."""
    trip = get_trip(trip_id)
    if "error" in trip:
        return trip
    rows = lakebase.run_query(
        """
        SELECT forecast_date,
               ROUND(MIN(temperature_c)::numeric, 1) AS min_temp_c,
               ROUND(MAX(temperature_c)::numeric, 1) AS max_temp_c,
               MAX(precipitation_prob)               AS max_precip_prob,
               ROUND(MAX(precipitation_mm)::numeric, 1) AS max_precip_mm,
               MAX(us_aqi)                           AS max_aqi
        FROM weather_snapshots
        WHERE destination_id = %s AND forecast_date BETWEEN %s AND %s
        GROUP BY forecast_date
        ORDER BY forecast_date
        """,
        (trip["destination_id"], trip["start_date"], trip["end_date"]),
    )
    for r in rows:
        prob = r["max_precip_prob"] or 0
        aqi = r["max_aqi"] or 0
        reasons = []
        if prob >= config.RAIN_PROB_THRESHOLD:
            reasons.append(f"rain likely ({prob}%)")
        if aqi >= config.AQI_THRESHOLD:
            reasons.append(f"poor air quality (AQI {aqi})")
        r["is_bad"] = bool(reasons)
        r["bad_reason"] = "; ".join(reasons) or None
    return rows


def search_activities(trip_id: int, query: str, top_k: int = 5,
                      indoor_only: bool = False) -> list[dict] | dict:
    """Semantic search over the destination's activities (pgvector cosine)."""
    trip = get_trip(trip_id)
    if "error" in trip:
        return trip
    qvec = models.embed_text(query)
    filt = "AND is_outdoor = FALSE" if indoor_only else ""
    return lakebase.run_query(
        f"""
        SELECT activity_id, name, category, is_outdoor,
               ROUND((1 - (requirements_embedding <=> %s::vector))::numeric, 3) AS similarity
        FROM activities
        WHERE destination_id = %s AND requirements_embedding IS NOT NULL {filt}
        ORDER BY requirements_embedding <=> %s::vector
        LIMIT %s
        """,
        (qvec, trip["destination_id"], qvec, top_k),
    )


# --------------------------------------------------------------- writes -----
def add_itinerary_item(trip_id: int, day_date: str, title: str, activity_id: int | None = None,
                       start_time: str | None = None, end_time: str | None = None,
                       notes: str | None = None) -> dict:
    rows = lakebase.run_write_returning(
        """
        INSERT INTO itinerary_items
            (trip_id, activity_id, day_date, start_time, end_time, title, notes, status, sort_order)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'planned',
                COALESCE((SELECT MAX(sort_order) + 1 FROM itinerary_items
                          WHERE trip_id = %s AND day_date = %s), 1))
        RETURNING item_id, trip_id, day_date, title, start_time, status
        """,
        (trip_id, activity_id, day_date, start_time, end_time, title, notes, trip_id, day_date),
    )
    return rows[0] if rows else {"error": "insert failed"}


def move_itinerary_item(item_id: int, day_date: str | None = None,
                        start_time: str | None = None, end_time: str | None = None,
                        reason: str | None = None) -> dict:
    rows = lakebase.run_write_returning(
        """
        UPDATE itinerary_items SET
            day_date          = COALESCE(%s, day_date),
            start_time        = COALESCE(%s, start_time),
            end_time          = COALESCE(%s, end_time),
            status            = 'rescheduled',
            reschedule_reason = COALESCE(%s, reschedule_reason),
            updated_at        = now()
        WHERE item_id = %s
        RETURNING item_id, day_date, start_time, title, status, reschedule_reason
        """,
        (day_date, start_time, end_time, reason, item_id),
    )
    return rows[0] if rows else {"error": f"item {item_id} not found"}


def remove_itinerary_item(item_id: int) -> dict:
    rows = lakebase.run_write_returning(
        "DELETE FROM itinerary_items WHERE item_id = %s RETURNING item_id, title",
        (item_id,),
    )
    return rows[0] if rows else {"error": f"item {item_id} not found"}


def add_packing_item(trip_id: int, item_name: str, category: str | None = None,
                     quantity: int = 1, reason: str | None = None) -> dict:
    rows = lakebase.run_write_returning(
        """
        INSERT INTO packing_items (trip_id, item_name, category, quantity, reason)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (trip_id, item_name) DO UPDATE
            SET quantity = EXCLUDED.quantity, reason = EXCLUDED.reason
        RETURNING packing_id, item_name, category, quantity, reason
        """,
        (trip_id, item_name, category, quantity, reason),
    )
    return rows[0] if rows else {"error": "insert failed"}


def list_packing(trip_id: int) -> list[dict]:
    return lakebase.run_query(
        """
        SELECT packing_id, item_name, category, quantity, reason, is_checked
        FROM packing_items WHERE trip_id = %s ORDER BY category, item_name
        """,
        (trip_id,),
    )


# ----------------------------------------------------- composite (smart) -----
def generate_itinerary(trip_id: int, activities_per_day: int = 3, replace: bool = True) -> dict:
    """Build a day-by-day plan: weather-appropriate, interest-matched activities."""
    trip = get_trip(trip_id)
    if "error" in trip:
        return trip
    if replace:
        lakebase.run_write("DELETE FROM itinerary_items WHERE trip_id = %s", (trip_id,))

    day_weather = {d["forecast_date"]: d for d in weather_by_day(trip_id)}
    urow = lakebase.run_query(
        "SELECT interests FROM users u JOIN trips t ON t.user_id = u.user_id WHERE t.trip_id = %s",
        (trip_id,),
    )
    interests = " ".join(urow[0]["interests"]) if urow and urow[0].get("interests") else "sightseeing"

    times = [("09:00", "11:00"), ("12:30", "14:30"), ("15:30", "17:00"),
             ("18:00", "19:30")]
    used: set[int] = set()
    created = []
    day = trip["start_date"]
    while day <= trip["end_date"]:
        w = day_weather.get(day)
        indoor_only = bool(w and w["is_bad"])
        query = f"{interests} {'indoor' if indoor_only else 'outdoor and indoor'} activities"
        cands = search_activities(trip_id, query, top_k=activities_per_day + len(used),
                                  indoor_only=indoor_only)
        picks = [c for c in cands if c["activity_id"] not in used][:activities_per_day]
        for idx, c in enumerate(picks):
            st, et = times[idx] if idx < len(times) else (None, None)
            add_itinerary_item(trip_id, str(day), c["name"], c["activity_id"], st, et,
                               notes=("Indoor pick due to weather" if indoor_only else None))
            used.add(c["activity_id"])
            created.append({"day": str(day), "title": c["name"],
                            "indoor": not c["is_outdoor"]})
        day = day + timedelta(days=1)
    return {"trip": trip["title"], "created": len(created), "items": created}


def reschedule_outdoor_for_weather(trip_id: int) -> dict:
    """Move outdoor items off bad days to a good day; if none, swap to an indoor pick."""
    trip = get_trip(trip_id)
    if "error" in trip:
        return trip
    days = weather_by_day(trip_id)
    good_days = [d["forecast_date"] for d in days if not d["is_bad"]]
    bad = {d["forecast_date"]: d for d in days if d["is_bad"]}
    changes = []
    for it in list_itinerary(trip_id):
        if not it["is_outdoor"] or it["day_date"] not in bad:
            continue
        reason_day = bad[it["day_date"]]["bad_reason"]
        if good_days:
            target = good_days[0]
            move_itinerary_item(it["item_id"], day_date=str(target),
                                reason=f"Moved from {it['day_date']} to {target}: {reason_day}.")
            changes.append({"item_id": it["item_id"], "title": it["title"], "action": "moved",
                            "from": str(it["day_date"]), "to": str(target), "reason": reason_day})
        else:
            alts = search_activities(trip_id, f"indoor alternative to {it['title']}",
                                     top_k=3, indoor_only=True)
            alt = alts[0] if isinstance(alts, list) and alts else None
            if alt:
                lakebase.run_write(
                    """
                    UPDATE itinerary_items
                    SET activity_id = %s, title = %s, status = 'rescheduled',
                        reschedule_reason = %s, updated_at = now()
                    WHERE item_id = %s
                    """,
                    (alt["activity_id"], alt["name"],
                     f"Swapped outdoor '{it['title']}' for indoor '{alt['name']}': {reason_day}.",
                     it["item_id"]),
                )
                changes.append({"item_id": it["item_id"], "action": "swapped",
                                "from_title": it["title"], "to_title": alt["name"],
                                "reason": reason_day})
            else:
                move_itinerary_item(it["item_id"],
                                    reason=f"Flagged: {reason_day}. No indoor alternative found.")
                changes.append({"item_id": it["item_id"], "action": "flagged",
                                "title": it["title"], "reason": reason_day})
    return {"trip": trip["title"], "bad_days": [str(d) for d in bad],
            "changed_count": len(changes), "changes": changes}


def build_packing_list(trip_id: int) -> dict:
    """Generate a weather- and activity-aware packing list and store it."""
    trip = get_trip(trip_id)
    if "error" in trip:
        return trip
    days = weather_by_day(trip_id)
    acts = lakebase.run_query(
        """
        SELECT DISTINCT COALESCE(a.is_outdoor, FALSE) AS is_outdoor, a.category, a.tags
        FROM itinerary_items i JOIN activities a ON a.activity_id = i.activity_id
        WHERE i.trip_id = %s
        """,
        (trip_id,),
    )
    suggestions = [
        ("Reusable water bottle", "gear", "Staying hydrated on active days."),
        ("Phone + charger", "gear", "Everyday essential."),
        ("ID / travel documents", "documents", "Required for travel and entry."),
        ("Sunscreen SPF50", "health", "UV protection outdoors."),
    ]
    if any((d["max_precip_prob"] or 0) >= config.RAIN_PROB_THRESHOLD for d in days):
        suggestions.append(("Rain jacket", "clothing", "Rain forecast on at least one day."))
    if any((d["max_aqi"] or 0) >= config.AQI_THRESHOLD for d in days):
        suggestions.append(("N95 mask", "health", "Air quality forecast to be poor."))
    if any((d["min_temp_c"] or 99) <= 8 for d in days):
        suggestions.append(("Warm fleece layer", "clothing", "Cold temperatures forecast."))
    if any((d["max_temp_c"] or 0) >= 25 for d in days):
        suggestions.append(("Sun hat", "clothing", "Hot afternoons forecast."))

    cats = {a["category"] for a in acts}
    if any(a["is_outdoor"] for a in acts):
        suggestions.append(("Hiking shoes", "gear", "Outdoor trails on the itinerary."))
    if {"wellness", "water"} & cats:
        suggestions.append(("Swimwear", "clothing", "Water/wellness activity planned."))

    written = [add_packing_item(trip_id, n, c, 1, r) for n, c, r in suggestions]
    return {"trip": trip["title"], "added": len(written), "items": written}
