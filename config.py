"""
Central configuration, driven entirely by environment variables so no secrets
or workspace-specific values are hard-coded. Defaults match the verified
Databricks stack: GTE-Large-en embeddings (1024-dim) + Llama-3.3-70B chat via
Foundation Model APIs, and pgvector inside Lakebase.
"""

import os

# --- Unity Catalog (Delta bronze/silver tables written by the Spark pipeline)
UC_CATALOG = os.environ.get("UC_CATALOG", "main")
UC_SCHEMA = os.environ.get("UC_SCHEMA", "trip_planner")

# --- Lakebase secret scope (matches app.yaml / Day-1 convention) ------------
LAKEBASE_SECRET_SCOPE = os.environ.get("LAKEBASE_SECRET_SCOPE", "database")
LAKEBASE_SECRET_KEY = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")

# --- Foundation Model API endpoints (Databricks-hosted, OpenAI-compatible) --
# Endpoint names per the Foundation Model APIs supported-models list.
EMBEDDING_ENDPOINT = os.environ.get("EMBEDDING_ENDPOINT", "databricks-gte-large-en")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1024"))
CHAT_ENDPOINT = os.environ.get("CHAT_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")

# --- External APIs (no key required for Open-Meteo non-commercial use) -------
OPEN_METEO_GEOCODING = os.environ.get(
    "OPEN_METEO_GEOCODING", "https://geocoding-api.open-meteo.com/v1/search"
)
OPEN_METEO_FORECAST = os.environ.get(
    "OPEN_METEO_FORECAST", "https://api.open-meteo.com/v1/forecast"
)
OPEN_METEO_AIR_QUALITY = os.environ.get(
    "OPEN_METEO_AIR_QUALITY", "https://air-quality-api.open-meteo.com/v1/air-quality"
)
WIKIMEDIA_API = os.environ.get("WIKIMEDIA_API", "https://en.wikipedia.org/w/api.php")
WIKIPEDIA_REST = os.environ.get(
    "WIKIPEDIA_REST", "https://en.wikipedia.org/api/rest_v1"
)
# Wikimedia asks for a descriptive User-Agent with contact info.
WIKIMEDIA_USER_AGENT = os.environ.get(
    "WIKIMEDIA_USER_AGENT",
    "databricks-trip-planner/1.0 (https://github.com/Divyangana7/databricks-trip-planner)",
)

# --- Retrieval / agent tuning ----------------------------------------------
RETRIEVAL_TOP_K = int(os.environ.get("RETRIEVAL_TOP_K", "5"))
# Thresholds the agent uses to decide an outdoor item is unsafe.
RAIN_PROB_THRESHOLD = int(os.environ.get("RAIN_PROB_THRESHOLD", "60"))       # %
PRECIP_MM_THRESHOLD = float(os.environ.get("PRECIP_MM_THRESHOLD", "2.0"))     # mm/hr
AQI_THRESHOLD = int(os.environ.get("AQI_THRESHOLD", "100"))                   # US AQI
