"""
One-time secret setup. Run from a Databricks notebook cell:

    %sh python setup_secrets.py

Stores the Lakebase connection URL in a Databricks secret scope so it never
appears in code, config, or notebook output. Uses getpass so nothing is echoed
to the screen or shell history.

This project needs only ONE secret: the Lakebase URL. Open-Meteo and Wikimedia
require no API key for non-commercial use, so there is nothing else to store.
"""

import getpass

from databricks.sdk import WorkspaceClient

SCOPE = "database"
KEY = "lakebase-url"

w = WorkspaceClient()

# Create the scope if it doesn't exist (ignore "already exists").
try:
    w.secrets.create_scope(scope=SCOPE)
    print(f"Created secret scope: {SCOPE}")
except Exception as exc:
    print(f"Scope {SCOPE} may already exist: {exc}")

lakebase_url = getpass.getpass(
    "Paste your Lakebase connection URL "
    "(postgresql://role:password@host:5432/databricks_postgres?sslmode=require): "
).strip()

if not lakebase_url:
    raise SystemExit("No URL entered — aborting without writing a secret.")

w.secrets.put_secret(scope=SCOPE, key=KEY, string_value=lakebase_url)
print(f"Stored secret {SCOPE}/{KEY}. You can now run the app and jobs.")
