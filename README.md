# AI Trip & Outdoor Activity Planner — Databricks Capstone

A Databricks app where users save destinations and preferences, then ask an AI
agent to build a **weather-aware itinerary**. The agent retrieves suitable
activities semantically and takes real write actions (add / move / reschedule
itinerary items, build packing lists) against Lakebase.

Built on the Day-1 Lakebase-app pattern (Flask + Lakebase/Postgres + Databricks
secret scopes), extended for the capstone requirements.

## Verified stack

| Layer | Choice | Why |
|---|---|---|
| OLTP + vectors | **Lakebase (Postgres) + pgvector** | Store embeddings and query them in SQL; no separate vector DB. |
| Embeddings | **`databricks-gte-large-en`** (1024-dim) via Foundation Model APIs | Native, OpenAI-compatible, documented 1024-dim output. |
| Agent LLM | **`databricks-meta-llama-3-3-70b-instruct`** via Foundation Model APIs | Native tool-calling chat endpoint. |
| Pipeline | **Spark** → Delta (bronze/silver) → Lakebase | Satisfies "data pipeline in Spark". |
| APIs | **Open-Meteo** (geocoding, weather, air quality) + **Wikimedia** | No API key for non-commercial use. |
| App | **Flask + Databricks Apps** | Same deploy pattern as Day-1. |

Sources: Databricks Foundation Model APIs supported-models docs; Lakebase
pgvector / `lakebase_vector` docs; the Lakebase + Foundation Model APIs +
pgvector reference blog; Open-Meteo and Wikimedia API docs.

## How each capstone requirement is met

| Requirement | Where |
|---|---|
| Data pipeline in Spark | `pipeline/ingest_spark.py` |
| ≥1 third-party API | `clients/open_meteo.py`, `clients/wikimedia.py` |
| Unstructured data → embeddings | `pipeline/embed_job.py` (descriptions, attractions, activity requirements, user notes) |
| Databricks App + frontend | `app.py` + `templates/index.html` |
| AI agent with read **and** write tools | `agent/tools.py`, `agent/agent.py` |
| 7 Lakebase tables, seeded | `sql/schema.sql`, `sql/seed.sql`, `sql/verify.sql` |

## Repo layout (built in phases)

```
sql/schema.sql      sql/seed.sql      sql/verify.sql     [Phase 1 — DONE]
config.py  lakebase.py  setup_secrets.py  requirements.txt [Phase 1 — DONE]
models.py                                                 [Phase 2 — DONE] FMA embed/chat client
clients/open_meteo.py  clients/wikimedia.py               [Phase 2 — DONE] API clients
notebooks/01_smoke_test_clients.py                        [Phase 2 — DONE] live API + model checks
pipeline/ingest_spark.py  pipeline/embed_job.py           [Phase 3] Spark + embeddings
agent/tools.py  agent/agent.py                            [Phase 4] agent + tools
app.py  app.yaml  templates/index.html                    [Phase 5] Databricks App
evidence/RUNBOOK.md                                       [Phase 6] screenshot-by-screenshot
```

## Phase 1 runbook (do this now)

1. **Create the Lakebase instance** and a **native-password role** (see Day-1
   README steps 2). Copy the connection URL.
2. **Store the secret** — from a Databricks notebook:
   ```
   %sh python setup_secrets.py
   ```
   Paste the Lakebase URL when prompted. (Only secret this project needs.)
3. **Create the schema** — in a Databricks SQL editor connected to Lakebase, or
   via `psql "$LAKEBASE_URL"`, run `sql/schema.sql`.
4. **Seed** — run `sql/seed.sql`.
5. **Verify** — run `sql/verify.sql`. **Screenshot the query 1 result** (counts
   vs. minimums, all `pass = true`) — that is your "tables seeded to minimums"
   evidence. Embedding coverage (query 5) will show 0% now; it becomes 100%
   after Phase 3.

Do not commit `.env`. Confirm the repo has no secrets before pushing.

## Security

- The only credential (Lakebase URL) lives in a Databricks secret scope, read at
  runtime via the Databricks SDK. It is never in code, `app.yaml`, or `.env`
  committed to git. Keep it out of screenshots.
