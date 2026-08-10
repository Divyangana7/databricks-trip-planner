"""
Databricks App — AI Trip & Outdoor Activity Planner.

Serves a single-page map UI (templates/index.html) plus a small JSON API that
reads trip/weather/itinerary/packing state from Lakebase and runs the agent.
Same deploy pattern as the Day-1 Lakebase app (Flask + secret scopes).
"""

import datetime
import decimal
import logging
import os

from flask import Flask, jsonify, render_template, request

import lakebase
from agent import agent, tools

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trip-planner.app")

app = Flask(__name__)


def _clean(value):
    """Make Lakebase rows JSON-serializable (Decimal->float, date/time->str)."""
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.time, datetime.datetime)):
        return str(value)
    return value


def _current_user() -> str:
    email = request.headers.get("X-Forwarded-Email")
    return email or "you@example.com"


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/bootstrap")
def bootstrap():
    """Everything the page needs on load: destinations (map) + trips."""
    destinations = lakebase.run_query(
        """
        SELECT destination_id, name, country, latitude, longitude,
               LEFT(COALESCE(description, ''), 240) AS blurb
        FROM destinations ORDER BY name
        """
    )
    trips = lakebase.run_query(
        """
        SELECT t.trip_id, t.title, t.start_date, t.end_date, t.status,
               d.destination_id, d.name AS destination, d.country,
               d.latitude, d.longitude
        FROM trips t JOIN destinations d ON d.destination_id = t.destination_id
        ORDER BY t.trip_id
        """
    )
    return jsonify(_clean({"destinations": destinations, "trips": trips,
                           "user": _current_user()}))


@app.route("/api/trip/<int:trip_id>")
def trip_detail(trip_id):
    return jsonify(_clean({
        "trip": tools.get_trip(trip_id),
        "weather": tools.weather_by_day(trip_id),
        "itinerary": tools.list_itinerary(trip_id),
        "packing": tools.list_packing(trip_id),
    }))


@app.route("/api/agent", methods=["POST"])
def run_agent():
    body = request.get_json(force=True) or {}
    trip_id = int(body.get("trip_id"))
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Type a request for the planner."}), 400
    result = agent.run_agent(message, trip_id=trip_id)
    # Return the reply + tool trace + refreshed state so the UI updates live.
    return jsonify(_clean({
        "reply": result["reply"],
        "tools": [s["tool"] for s in result["steps"]],
        "trip": tools.get_trip(trip_id),
        "weather": tools.weather_by_day(trip_id),
        "itinerary": tools.list_itinerary(trip_id),
        "packing": tools.list_packing(trip_id),
    }))


if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.environ.get("DATABRICKS_APP_PORT")
               or os.environ.get("FLASK_RUN_PORT") or 8000)
    app.run(debug=False, host=host, port=port)
