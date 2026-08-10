"""
Wikimedia client — destination descriptions + nearby attractions.

Rate-limit hardened for shared-IP environments (Databricks clusters):
  * One shared Session with a descriptive User-Agent (Wikimedia UA policy).
  * Retry with exponential backoff that honors the Retry-After header on 429/503.
  * nearby_attractions() uses a SINGLE generator query (geosearch + extracts +
    coordinates in one request) instead of 1 + N requests.

Endpoints:
  * Page summary:  GET https://en.wikipedia.org/api/rest_v1/page/summary/{title}
  * Action API:    GET https://en.wikipedia.org/w/api.php  (list/generator=geosearch)
Docs: https://www.mediawiki.org/wiki/API:Main_page
"""

import logging
import random
import time
from urllib.parse import quote

import requests

import config

logger = logging.getLogger("trip-planner.wikimedia")
TIMEOUT = 30
_MAX_RETRIES = 5
_MIN_INTERVAL = 0.2  # polite spacing between calls (seconds)

_session = requests.Session()
_last_call = 0.0


def _headers() -> dict:
    return {"User-Agent": config.WIKIMEDIA_USER_AGENT, "Accept": "application/json"}


def _get(url: str, params: dict | None = None) -> requests.Response:
    """GET with polite spacing + backoff on 429/503. Caller inspects status."""
    global _last_call
    for attempt in range(_MAX_RETRIES):
        gap = time.time() - _last_call
        if gap < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - gap)
        resp = _session.get(url, params=params, headers=_headers(), timeout=TIMEOUT)
        _last_call = time.time()
        if resp.status_code in (429, 503):
            ra = resp.headers.get("Retry-After")
            wait = float(ra) if (ra and ra.isdigit()) else min(2 ** attempt, 30) + random.random()
            logger.warning("Wikimedia %s; backing off %.1fs (attempt %d/%d)",
                           resp.status_code, wait, attempt + 1, _MAX_RETRIES)
            time.sleep(wait)
            continue
        return resp
    return resp  # last response (still 429/503) — caller will raise


def summary(title: str) -> dict | None:
    """Fetch a page summary (short description + extract). None if not found."""
    url = f"{config.WIKIPEDIA_REST}/page/summary/{quote(title.replace(' ', '_'))}"
    resp = _get(url)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return {
        "title": data.get("title"),
        "description": data.get("description"),
        "extract": data.get("extract"),
        "url": (data.get("content_urls", {}).get("desktop", {}) or {}).get("page"),
        "latitude": (data.get("coordinates") or {}).get("lat"),
        "longitude": (data.get("coordinates") or {}).get("lon"),
    }


def geosearch(latitude: float, longitude: float, radius_m: int = 10000,
              limit: int = 10) -> list[dict]:
    """Titles of Wikipedia articles near a coordinate (one request, no extracts)."""
    params = {
        "action": "query", "list": "geosearch",
        "gscoord": f"{latitude}|{longitude}", "gsradius": radius_m,
        "gslimit": limit, "format": "json",
    }
    resp = _get(config.WIKIMEDIA_API, params)
    resp.raise_for_status()
    hits = resp.json().get("query", {}).get("geosearch", []) or []
    return [
        {"pageid": h.get("pageid"), "title": h.get("title"),
         "latitude": h.get("lat"), "longitude": h.get("lon"),
         "distance_m": h.get("dist")}
        for h in hits
    ]


def nearby_attractions(latitude: float, longitude: float, radius_m: int = 10000,
                       limit: int = 8) -> list[dict]:
    """Nearby articles WITH intro extract + coordinates in a SINGLE API request.

    Uses geosearch as a generator (prefix 'ggs') plus prop=extracts|coordinates,
    so we don't make one summary call per result (the cause of 429 storms).
    """
    params = {
        "action": "query", "format": "json",
        "generator": "geosearch",
        "ggscoord": f"{latitude}|{longitude}",
        "ggsradius": radius_m,
        "ggslimit": limit,
        "prop": "extracts|coordinates|description",
        "exintro": 1, "explaintext": 1, "exlimit": "max",
    }
    resp = _get(config.WIKIMEDIA_API, params)
    resp.raise_for_status()
    pages = (resp.json().get("query", {}) or {}).get("pages", {}) or {}

    def _dist_m(plat, plon):
        # rough great-circle distance in metres (for display/sort only)
        import math
        if plat is None or plon is None:
            return None
        dlat = math.radians(plat - latitude)
        dlon = math.radians(plon - longitude)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(latitude)) * math.cos(math.radians(plat)) * math.sin(dlon / 2) ** 2)
        return round(6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

    rows = []
    for p in pages.values():
        coord = (p.get("coordinates") or [{}])[0]
        plat, plon = coord.get("lat", latitude), coord.get("lon", longitude)
        rows.append({
            "pageid": p.get("pageid"),
            "title": p.get("title"),
            "index": p.get("index", 9999),           # generator order = by distance
            "latitude": plat,
            "longitude": plon,
            "distance_m": _dist_m(plat, plon),
            "description": p.get("description"),
            "extract": p.get("extract"),
        })
    rows.sort(key=lambda r: r["index"])
    for r in rows:
        r.pop("index", None)
    return rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("summary:", (summary("Banff, Alberta") or {}).get("description"))
    near = nearby_attractions(51.1784, -115.5708, radius_m=8000, limit=5)
    print(f"nearby ({len(near)}):", [n["title"] for n in near])
