"""
Destination catalog + bootstrap.

Scales the project from 3 to 25 outdoor destinations across many countries.
`bootstrap()` geocodes each name via Open-Meteo, pulls a Wikipedia description,
and inserts it into Lakebase — but only if that name isn't already present, so
the original seed rows are preserved and re-runs don't duplicate.
"""

import logging

import lakebase
from clients import open_meteo, wikimedia

logger = logging.getLogger("trip-planner.destinations")

# (name, country) — chosen for outdoor variety + country diversity (good for
# distinct-value metrics). Geocoding resolves the coordinates/timezone.
CATALOG = [
    ("Banff", "Canada"), ("Whistler", "Canada"),
    ("Kyoto", "Japan"), ("Springdale", "United States"),
    ("Moab", "United States"), ("Sedona", "United States"),
    ("Flagstaff", "United States"), ("Asheville", "United States"),
    ("Boulder", "United States"), ("Jackson", "United States"),
    ("Reykjavik", "Iceland"), ("Queenstown", "New Zealand"),
    ("Interlaken", "Switzerland"), ("Zermatt", "Switzerland"),
    ("Chamonix", "France"), ("Cusco", "Peru"),
    ("Cape Town", "South Africa"), ("Puerto Natales", "Chile"),
    ("Ubud", "Indonesia"), ("Pokhara", "Nepal"),
    ("Chiang Mai", "Thailand"), ("Ljubljana", "Slovenia"),
    ("Bergen", "Norway"), ("Innsbruck", "Austria"),
    ("Hobart", "Australia"),
]


def _exists(name: str) -> bool:
    return bool(lakebase.run_query("SELECT 1 FROM destinations WHERE name = %s", (name,)))


def bootstrap(catalog: list[tuple] | None = None) -> dict:
    """Geocode + describe + insert any catalog destinations not already present."""
    catalog = catalog or CATALOG
    inserted, skipped, failed = [], [], []
    for name, country in catalog:
        if _exists(name):
            skipped.append(name)
            continue
        try:
            matches = open_meteo.geocode(name, count=1)
            if not matches:
                failed.append((name, "no geocode match"))
                continue
            g = matches[0]
            info = wikimedia.summary(name) or {}
            rows = lakebase.run_write_returning(
                """
                INSERT INTO destinations
                    (name, country, admin_region, latitude, longitude, timezone,
                     description, wikipedia_url, source)
                SELECT %s, %s, %s, %s, %s, %s, %s, %s, 'phase8-bootstrap'
                WHERE NOT EXISTS (SELECT 1 FROM destinations WHERE name = %s)
                RETURNING destination_id, name
                """,
                (name, g.get("country") or country, g.get("admin_region"),
                 g.get("latitude"), g.get("longitude"), g.get("timezone"),
                 info.get("extract") or info.get("description"),
                 info.get("url"), name),
            )
            if rows:
                inserted.append(rows[0]["name"])
        except Exception as exc:  # keep going; report at the end
            logger.warning("bootstrap failed for %s: %s", name, exc)
            failed.append((name, str(exc)))
    total = lakebase.run_query("SELECT COUNT(*) AS n FROM destinations")[0]["n"]
    return {"inserted": len(inserted), "skipped": len(skipped),
            "failed": failed, "total_destinations": total,
            "inserted_names": inserted}
