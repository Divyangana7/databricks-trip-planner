"""
Wikimedia client — destination descriptions + nearby attractions.

Uses two public Wikimedia endpoints (no key; a descriptive User-Agent is asked for):
  * Page summary (description/extract):
      GET https://en.wikipedia.org/api/rest_v1/page/summary/{title}
  * Nearby attractions (Action API, list=geosearch):
      GET https://en.wikipedia.org/w/api.php?action=query&list=geosearch
          &gscoord=<lat>|<lon>&gsradius=<m>&gslimit=<n>&format=json

Docs: https://www.mediawiki.org/wiki/API:Main_page
"""

import logging
from urllib.parse import quote

import requests

import config

logger = logging.getLogger("trip-planner.wikimedia")
TIMEOUT = 30


def _headers() -> dict:
    return {"User-Agent": config.WIKIMEDIA_USER_AGENT, "Accept": "application/json"}


def summary(title: str) -> dict | None:
    """Fetch a page summary (short description + extract). None if not found."""
    url = f"{config.WIKIPEDIA_REST}/page/summary/{quote(title.replace(' ', '_'))}"
    resp = requests.get(url, headers=_headers(), timeout=TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return {
        "title": data.get("title"),
        "description": data.get("description"),      # short one-liner
        "extract": data.get("extract"),              # paragraph (unstructured text to embed)
        "url": (data.get("content_urls", {}).get("desktop", {}) or {}).get("page"),
        "latitude": (data.get("coordinates") or {}).get("lat"),
        "longitude": (data.get("coordinates") or {}).get("lon"),
    }


def geosearch(latitude: float, longitude: float, radius_m: int = 10000,
              limit: int = 10) -> list[dict]:
    """Find Wikipedia articles for places near a coordinate (nearby attractions)."""
    params = {
        "action": "query",
        "list": "geosearch",
        "gscoord": f"{latitude}|{longitude}",
        "gsradius": radius_m,        # metres, max 10000
        "gslimit": limit,            # max 500
        "format": "json",
    }
    resp = requests.get(config.WIKIMEDIA_API, params=params, headers=_headers(), timeout=TIMEOUT)
    resp.raise_for_status()
    hits = resp.json().get("query", {}).get("geosearch", []) or []
    return [
        {
            "pageid": h.get("pageid"),
            "title": h.get("title"),
            "latitude": h.get("lat"),
            "longitude": h.get("lon"),
            "distance_m": h.get("dist"),
        }
        for h in hits
    ]


def nearby_attractions(latitude: float, longitude: float, radius_m: int = 10000,
                       limit: int = 8) -> list[dict]:
    """Geosearch, then enrich each hit with its summary/extract (for embedding)."""
    out = []
    for hit in geosearch(latitude, longitude, radius_m, limit):
        info = summary(hit["title"]) or {}
        out.append({**hit,
                    "description": info.get("description"),
                    "extract": info.get("extract"),
                    "url": info.get("url")})
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("summary:", summary("Banff, Alberta"))
    near = nearby_attractions(51.1784, -115.5708, radius_m=8000, limit=3)
    print(f"nearby ({len(near)}):", [n["title"] for n in near])
