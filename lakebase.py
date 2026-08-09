"""
Lakebase (Databricks-managed Postgres) connection helper.

Extends the Day-1 boilerplate pattern (single LAKEBASE_URL secret -> psycopg2 +
SQLAlchemy) with pgvector support so embeddings can be written and queried as
native VECTOR columns. Same public surface as Day-1: get_connection(),
get_engine(), run_query(), run_write() — plus run_write_returning() and
register of the pgvector type on each connection.
"""

import base64
import logging
import os
from contextlib import contextmanager

import psycopg2
from databricks.sdk import WorkspaceClient
from psycopg2.extras import RealDictCursor, execute_values
from sqlalchemy import create_engine

logger = logging.getLogger("trip-planner.lakebase")

_w = WorkspaceClient()
_SCOPE = os.environ.get("LAKEBASE_SECRET_SCOPE", "database")
_KEY = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")


def _lakebase_url() -> str:
    """Fetch and decode the Lakebase connection URL from the Databricks secret scope.

    Falls back to a local LAKEBASE_URL env var for local development.
    """
    env_url = os.environ.get("LAKEBASE_URL")
    if env_url:
        return env_url
    secret = _w.secrets.get_secret(scope=_SCOPE, key=_KEY)
    return base64.b64decode(secret.value).decode("utf-8")


def _register_vector(conn) -> None:
    """Register the pgvector type adapter so Python lists round-trip as VECTOR.

    No-op if pgvector isn't installed yet (e.g. before schema.sql runs).
    """
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
    except Exception as exc:  # extension not present yet / package missing
        logger.debug("pgvector not registered: %s", exc)


@contextmanager
def get_connection():
    """Yield a raw psycopg2 connection with a RealDictCursor factory + pgvector."""
    conn = psycopg2.connect(_lakebase_url(), cursor_factory=RealDictCursor)
    _register_vector(conn)
    try:
        yield conn
    finally:
        conn.close()


def get_engine():
    """Return a SQLAlchemy engine for Lakebase (used by the Spark JDBC/psycopg path)."""
    return create_engine(_lakebase_url())


def run_query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    """Run a read query against Lakebase and return rows as list[dict]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def run_write(sql: str, params: tuple | dict | None = None) -> int:
    """Run an INSERT/UPDATE/DELETE against Lakebase, return affected row count."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount


def run_write_returning(sql: str, params: tuple | dict | None = None) -> list[dict]:
    """Run a write with a RETURNING clause and return the returned rows.

    Used by agent tools so a write action can immediately report what changed.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.commit()
            return rows


def run_values(sql: str, rows: list[tuple], template: str | None = None) -> int:
    """Batch insert/upsert via psycopg2.extras.execute_values. Returns row count."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, template=template)
            conn.commit()
            return cur.rowcount
