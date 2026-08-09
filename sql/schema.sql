-- ============================================================================
-- AI Trip & Outdoor Activity Planner — Lakebase (Postgres) schema
-- ----------------------------------------------------------------------------
-- Target: Databricks Lakebase (managed Postgres) with the pgvector extension.
-- Embeddings are GTE-Large-en (1024 dimensions) from Foundation Model APIs.
--
-- Run this once against your Lakebase instance (Databricks SQL editor connected
-- to Lakebase, or psql using your LAKEBASE_URL). It is idempotent.
-- ============================================================================

-- Vector similarity search directly in Lakebase.
CREATE EXTENSION IF NOT EXISTS vector;

-- ----------------------------------------------------------------------------
-- 1. users  — people who save destinations + preferences
--   Unstructured field embedded for retrieval: notes  -> notes_embedding
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id         BIGSERIAL PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    home_city       TEXT,
    interests       TEXT[]      NOT NULL DEFAULT '{}',   -- e.g. {hiking, museums, food}
    pace            TEXT        DEFAULT 'moderate',      -- relaxed | moderate | packed
    notes           TEXT,                                -- free-text preferences (unstructured)
    notes_embedding VECTOR(1024),                        -- filled by embed_job.py
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 2. destinations — places, with a Wikimedia description (unstructured)
--   Unstructured field embedded for retrieval: description -> description_embedding
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS destinations (
    destination_id      BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    country             TEXT,
    admin_region        TEXT,
    latitude            DOUBLE PRECISION NOT NULL,
    longitude           DOUBLE PRECISION NOT NULL,
    timezone            TEXT,
    description         TEXT,                            -- Wikimedia summary (unstructured)
    description_embedding VECTOR(1024),                  -- filled by embed_job.py
    wikipedia_url       TEXT,
    source              TEXT DEFAULT 'open-meteo+wikimedia',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, latitude, longitude)
);

-- ----------------------------------------------------------------------------
-- 3. trips — a user's trip to a destination over a date range
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trips (
    trip_id         BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    destination_id  BIGINT NOT NULL REFERENCES destinations(destination_id) ON DELETE RESTRICT,
    title           TEXT NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    status          TEXT NOT NULL DEFAULT 'planning',    -- planning | booked | complete
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS idx_trips_user ON trips(user_id);

-- ----------------------------------------------------------------------------
-- 4. activities — things to do at a destination
--   Unstructured field embedded for retrieval: requirements -> requirements_embedding
--   (also the attraction 'description' is embedded into the same column set)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activities (
    activity_id             BIGSERIAL PRIMARY KEY,
    destination_id          BIGINT REFERENCES destinations(destination_id) ON DELETE CASCADE,
    name                    TEXT NOT NULL,
    category                TEXT,                        -- hiking | museum | food | landmark ...
    description             TEXT,                        -- attraction info (unstructured, Wikimedia)
    requirements            TEXT,                        -- gear/fitness/weather notes (unstructured)
    requirements_embedding  VECTOR(1024),                -- filled by embed_job.py
    is_outdoor              BOOLEAN NOT NULL DEFAULT FALSE,
    weather_sensitive       BOOLEAN NOT NULL DEFAULT FALSE,
    typical_duration_min    INTEGER,
    latitude                DOUBLE PRECISION,
    longitude               DOUBLE PRECISION,
    tags                    TEXT[] NOT NULL DEFAULT '{}',
    source                  TEXT DEFAULT 'wikimedia',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activities_dest ON activities(destination_id);

-- ----------------------------------------------------------------------------
-- 5. itinerary_items — the scheduled plan. PRIMARY agent WRITE target.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS itinerary_items (
    item_id             BIGSERIAL PRIMARY KEY,
    trip_id             BIGINT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
    activity_id         BIGINT REFERENCES activities(activity_id) ON DELETE SET NULL,
    day_date            DATE NOT NULL,
    start_time          TIME,
    end_time            TIME,
    title               TEXT NOT NULL,
    notes               TEXT,
    status              TEXT NOT NULL DEFAULT 'planned',  -- planned | rescheduled | cancelled | done
    reschedule_reason   TEXT,                             -- why the agent moved it (weather/AQI)
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_itin_trip_day ON itinerary_items(trip_id, day_date, sort_order);

-- ----------------------------------------------------------------------------
-- 6. weather_snapshots — hourly forecast + air quality per destination/date.
--   Written by the Spark ingestion pipeline from Open-Meteo.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_snapshots (
    snapshot_id         BIGSERIAL PRIMARY KEY,
    destination_id      BIGINT NOT NULL REFERENCES destinations(destination_id) ON DELETE CASCADE,
    captured_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    forecast_ts         TIMESTAMPTZ NOT NULL,             -- the hour this forecast is for
    forecast_date       DATE NOT NULL,
    temperature_c       DOUBLE PRECISION,
    precipitation_mm    DOUBLE PRECISION,
    precipitation_prob  INTEGER,
    wind_kph            DOUBLE PRECISION,
    us_aqi              INTEGER,
    pm2_5               DOUBLE PRECISION,
    pm10                DOUBLE PRECISION,
    uv_index            DOUBLE PRECISION,
    source              TEXT DEFAULT 'open-meteo',
    raw                 JSONB,
    UNIQUE (destination_id, forecast_ts)
);
CREATE INDEX IF NOT EXISTS idx_weather_dest_date ON weather_snapshots(destination_id, forecast_date);

-- ----------------------------------------------------------------------------
-- 7. packing_items — packing list per trip. Agent WRITE target.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS packing_items (
    packing_id      BIGSERIAL PRIMARY KEY,
    trip_id         BIGINT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
    item_name       TEXT NOT NULL,
    category        TEXT,                                 -- clothing | gear | documents | health
    quantity        INTEGER NOT NULL DEFAULT 1,
    reason          TEXT,                                 -- why the agent suggested it
    is_checked      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trip_id, item_name)
);

-- ----------------------------------------------------------------------------
-- Vector (pgvector) indexes — cosine distance for semantic retrieval.
-- IVFFlat needs ANALYZE + data present to be effective; safe to create now.
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_dest_desc_vec
    ON destinations USING hnsw (description_embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_act_req_vec
    ON activities USING hnsw (requirements_embedding vector_cosine_ops);
-- ----------------------------------------------------------------------------
-- OPTIONAL — Change Data Feed readiness (Lakebase CDF -> Unity Catalog Delta).
-- Uncomment if you also want the reverse pipeline (Lakebase -> lakehouse).
-- Each CDF-tracked table needs REPLICA IDENTITY FULL and >= 1 row.
-- ----------------------------------------------------------------------------
-- ALTER TABLE itinerary_items   REPLICA IDENTITY FULL;
-- ALTER TABLE packing_items     REPLICA IDENTITY FULL;
-- ALTER TABLE weather_snapshots REPLICA IDENTITY FULL;
