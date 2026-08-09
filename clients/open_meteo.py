"""
Open-Meteo API client — no API key required for non-commercial use.

Endpoints (verified against Open-Meteo docs):
  * Geocoding   GET https://geocoding-api.open-meteo.com/v1/search
  * Forecast    GET https://api.open-meteo.com/v1/forecast
  * Air Quality GET https://air-quality-api.open-meteo.com/v1/air-quality

Forecast + air-quality responses share the shape:
    { "hourly": { "time": [...], "<var>": [...] }, "hourly_units": {...} }
so we zip the parallel arrays into a list of per-hour dicts.
"""

import logging

import requests

import config

logger = logging.getLogger("trip-planner.open_meteo")
TIMEOUT = 30

# Default units: temperature_2m °C, precipitation mm, precipitation_probability %,
# wind_speed_10m km/h (Open-Meteo defaults).
_FORECAST_HOURLY = "temperature_2m,precipitation,precipitation_probability,wind_speed_10m"
_AQ_HOURLY = "pm2_5,pm10,us_aqi,uv_index"


def geocode(name: str, count: int = 1, language: str = "en") -> list[dict]:
    """Resolve a destination name to coordinates. Returns [] if no match."""
    resp = requests.get(
        config.OPEN_METEO_GEOCODING,
        params={"name": name, "count": count, "language": language, "format": "json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    return [
        {
            "name": r.get("name"),
            "country": r.get("country"),
            "admin_region": r.get("admin1"),
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
            "timezone": r.get("timezone"),
        }
        for r in results
    ]


def _zip_hourly(payload: dict) -> list[dict]:
    """Turn Open-Meteo's parallel hourly arrays into a list of per-hour dicts."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    rows = []
    for i, ts in enumerate(times):
        row = {"time": ts}
        for var, values in hourly.items():
            if var == "time":
                continue
            row[var] = values[i] if i < len(values) else None
        rows.append(row)
    return rows


def forecast(latitude: float, longitude: float, start_date: str | None = None,
             end_date: str | None = None, timezone: str = "auto") -> list[dict]:
    """Hourly weather forecast. Dates are ISO yyyy-mm-dd (optional)."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": _FORECAST_HOURLY,
        "timezone": timezone,
    }
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    resp = requests.get(config.OPEN_METEO_FORECAST, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    out = []
    for row in _zip_hourly(resp.json()):
        out.append({
            "forecast_ts": row["time"],
            "temperature_c": row.get("temperature_2m"),
            "precipitation_mm": row.get("precipitation"),
            "precipitation_prob": row.get("precipitation_probability"),
            "wind_kph": row.get("wind_speed_10m"),
        })
    return out


def air_quality(latitude: float, longitude: float, start_date: str | None = None,
                end_date: str | None = None, timezone: str = "auto") -> list[dict]:
    """Hourly air quality (US AQI, particulates, UV)."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": _AQ_HOURLY,
        "timezone": timezone,
    }
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    resp = requests.get(config.OPEN_METEO_AIR_QUALITY, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    out = []
    for row in _zip_hourly(resp.json()):
        out.append({
            "forecast_ts": row["time"],
            "us_aqi": row.get("us_aqi"),
            "pm2_5": row.get("pm2_5"),
            "pm10": row.get("pm10"),
            "uv_index": row.get("uv_index"),
        })
    return out


def weather_snapshots(latitude: float, longitude: float, start_date: str | None = None,
                      end_date: str | None = None, timezone: str = "auto") -> list[dict]:
    """Join forecast + air quality on the hour into weather_snapshots-shaped rows."""
    wx = {r["forecast_ts"]: r for r in forecast(latitude, longitude, start_date, end_date, timezone)}
    aq = {r["forecast_ts"]: r for r in air_quality(latitude, longitude, start_date, end_date, timezone)}
    merged = []
    for ts in sorted(set(wx) | set(aq)):
        row = {"forecast_ts": ts, "forecast_date": ts[:10]}
        row.update({k: v for k, v in wx.get(ts, {}).items() if k != "forecast_ts"})
        row.update({k: v for k, v in aq.get(ts, {}).items() if k != "forecast_ts"})
        merged.append(row)
    return merged


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    place = geocode("Banff", count=1)[0]
    print("geocoded:", place)
    rows = weather_snapshots(place["latitude"], place["longitude"])
    print(f"got {len(rows)} hourly snapshots; first:", rows[0] if rows else None)
