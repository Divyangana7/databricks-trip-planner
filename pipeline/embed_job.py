"""
Embedding job — the "process unstructured data" requirement.

Reads free text (destination descriptions, activity attraction text / requirements,
user preference notes), embeds it with GTE-Large (1024-dim) via Foundation Model
APIs, and writes the vectors back into the pgvector columns in Lakebase. After a
run, verify.sql query 5 (embedding coverage) flips from 0% to 100%.

Only rows whose embedding is still NULL are processed, so re-running is cheap and
idempotent.
"""

import logging

import lakebase
import models

logger = logging.getLogger("trip-planner.embed")


def _embed_and_update(select_sql: str, update_sql: str, text_fn) -> int:
    """Generic: select rows needing embeddings, embed, write vectors back."""
    rows = lakebase.run_query(select_sql)
    if not rows:
        return 0
    texts = [text_fn(r) for r in rows]
    vectors = models.embed_texts(texts)
    # get_connection() registers the pgvector adapter, so Python lists -> VECTOR.
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            for r, vec in zip(rows, vectors):
                cur.execute(update_sql, (vec, r["id"]))
        conn.commit()
    return len(rows)


def embed_destinations() -> int:
    return _embed_and_update(
        select_sql="""
            SELECT destination_id AS id, name, description
            FROM destinations
            WHERE description_embedding IS NULL
        """,
        update_sql="UPDATE destinations SET description_embedding = %s WHERE destination_id = %s",
        text_fn=lambda r: f"{r['name']}. {r.get('description') or ''}".strip(),
    )


def embed_activities() -> int:
    return _embed_and_update(
        select_sql="""
            SELECT activity_id AS id, name, category, description, requirements
            FROM activities
            WHERE requirements_embedding IS NULL
        """,
        update_sql="UPDATE activities SET requirements_embedding = %s WHERE activity_id = %s",
        # Combine the fields most useful for "find activities that fit interests + conditions".
        text_fn=lambda r: " ".join(filter(None, [
            r.get("name"), r.get("category"), r.get("requirements"), r.get("description"),
        ])),
    )


def embed_users() -> int:
    return _embed_and_update(
        select_sql="""
            SELECT user_id AS id, notes
            FROM users
            WHERE notes IS NOT NULL AND notes_embedding IS NULL
        """,
        update_sql="UPDATE users SET notes_embedding = %s WHERE user_id = %s",
        text_fn=lambda r: r.get("notes") or "",
    )


def coverage() -> list[dict]:
    """Embedding coverage per column (evidence for before/after)."""
    return lakebase.run_query("""
        SELECT 'destinations.description_embedding' AS col,
               COUNT(*) FILTER (WHERE description_embedding IS NOT NULL) AS filled,
               COUNT(*) AS total FROM destinations
        UNION ALL
        SELECT 'activities.requirements_embedding',
               COUNT(*) FILTER (WHERE requirements_embedding IS NOT NULL),
               COUNT(*) FROM activities
        UNION ALL
        SELECT 'users.notes_embedding',
               COUNT(*) FILTER (WHERE notes_embedding IS NOT NULL),
               COUNT(*) FROM users
    """)


def run() -> dict:
    d = embed_destinations()
    a = embed_activities()
    u = embed_users()
    logger.info("embedded destinations=%d activities=%d users=%d", d, a, u)
    return {"destinations": d, "activities": a, "users": u}
