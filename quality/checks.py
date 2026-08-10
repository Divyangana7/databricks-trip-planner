"""
Data-quality checks over Lakebase.

Each check returns a dict: {name, passed, detail, severity}. `run_all()` returns
the full report and an overall `passed` flag so a pipeline can gate on it
(fail-loud on data problems). Designed to be run from a notebook (see
notebooks/05_quality_checks.py) or wrapped by the pytest suite in tests/.

Severity:
  * "error"   -> must pass; gates the pipeline.
  * "warn"    -> informational; does not fail the gate.
"""

import logging

import config
import lakebase

logger = logging.getLogger("trip-planner.quality")

EMBEDDING_DIM = config.EMBEDDING_DIM  # 1024 for GTE-large-en


def _scalar(sql, params=None):
    rows = lakebase.run_query(sql, params)
    return list(rows[0].values())[0] if rows else None


def _check(name, passed, detail, severity="error"):
    return {"name": name, "passed": bool(passed), "detail": detail, "severity": severity}


# -- completeness -----------------------------------------------------------
def check_no_null_forecast_ts():
    n = _scalar("SELECT COUNT(*) FROM weather_snapshots WHERE forecast_ts IS NULL")
    return _check("weather.forecast_ts not null", n == 0, f"{n} null forecast_ts")


def check_destinations_have_coords():
    n = _scalar("SELECT COUNT(*) FROM destinations WHERE latitude IS NULL OR longitude IS NULL")
    return _check("destinations have coordinates", n == 0, f"{n} destinations missing lat/long")


# -- validity / ranges ------------------------------------------------------
def check_aqi_range():
    n = _scalar("SELECT COUNT(*) FROM weather_snapshots WHERE us_aqi IS NOT NULL AND (us_aqi < 0 OR us_aqi > 500)")
    return _check("us_aqi within 0-500", n == 0, f"{n} rows with out-of-range AQI")


def check_precip_prob_range():
    n = _scalar("SELECT COUNT(*) FROM weather_snapshots WHERE precipitation_prob IS NOT NULL AND (precipitation_prob < 0 OR precipitation_prob > 100)")
    return _check("precipitation_prob within 0-100", n == 0, f"{n} rows with out-of-range precip prob")


def check_trip_date_order():
    n = _scalar("SELECT COUNT(*) FROM trips WHERE end_date < start_date")
    return _check("trips end_date >= start_date", n == 0, f"{n} trips with end before start")


# -- embeddings -------------------------------------------------------------
def check_embedding_dim():
    """Every stored embedding must be exactly EMBEDDING_DIM long."""
    bad_dest = _scalar(
        "SELECT COUNT(*) FROM destinations WHERE description_embedding IS NOT NULL "
        "AND vector_dims(description_embedding) <> %s", (EMBEDDING_DIM,))
    bad_act = _scalar(
        "SELECT COUNT(*) FROM activities WHERE requirements_embedding IS NOT NULL "
        "AND vector_dims(requirements_embedding) <> %s", (EMBEDDING_DIM,))
    total = (bad_dest or 0) + (bad_act or 0)
    return _check(f"embeddings are {EMBEDDING_DIM}-dim", total == 0,
                  f"{total} embeddings with wrong dimension")


def check_embedding_coverage():
    missing = _scalar(
        "SELECT COUNT(*) FROM activities WHERE requirements_embedding IS NULL")
    return _check("all activities embedded", missing == 0,
                  f"{missing} activities without an embedding", severity="warn")


# -- referential integrity --------------------------------------------------
def check_no_orphans():
    orphans = _scalar(
        """
        SELECT
          (SELECT COUNT(*) FROM trips t LEFT JOIN destinations d ON d.destination_id=t.destination_id WHERE d.destination_id IS NULL)
        + (SELECT COUNT(*) FROM itinerary_items i LEFT JOIN trips t ON t.trip_id=i.trip_id WHERE t.trip_id IS NULL)
        + (SELECT COUNT(*) FROM weather_snapshots w LEFT JOIN destinations d ON d.destination_id=w.destination_id WHERE d.destination_id IS NULL)
        + (SELECT COUNT(*) FROM packing_items p LEFT JOIN trips t ON t.trip_id=p.trip_id WHERE t.trip_id IS NULL)
        """)
    return _check("no orphan foreign keys", orphans == 0, f"{orphans} orphan rows")


# -- uniqueness -------------------------------------------------------------
def check_weather_unique():
    dupes = _scalar(
        """
        SELECT COUNT(*) FROM (
          SELECT destination_id, forecast_ts FROM weather_snapshots
          GROUP BY destination_id, forecast_ts HAVING COUNT(*) > 1
        ) d
        """)
    return _check("weather_snapshots unique per (dest, hour)", dupes == 0,
                  f"{dupes} duplicate (destination_id, forecast_ts) keys")


CHECKS = [
    check_no_null_forecast_ts, check_destinations_have_coords,
    check_aqi_range, check_precip_prob_range, check_trip_date_order,
    check_embedding_dim, check_embedding_coverage,
    check_no_orphans, check_weather_unique,
]


def run_all() -> dict:
    results = [fn() for fn in CHECKS]
    errors = [r for r in results if r["severity"] == "error" and not r["passed"]]
    return {"passed": len(errors) == 0, "n_checks": len(results),
            "n_failed_errors": len(errors), "results": results}


def gate():
    """Raise if any error-severity check fails — use to fail a pipeline task."""
    report = run_all()
    if not report["passed"]:
        failed = [r["name"] for r in report["results"]
                  if r["severity"] == "error" and not r["passed"]]
        raise AssertionError(f"Data-quality gate failed: {failed}")
    return report
