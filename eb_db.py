"""
eb_db.py - Postgres (Supabase) connection for the cloud EarlyBird jobs.

Connection comes from env vars (GitHub Actions Secrets) or, locally, a 'supabase' block
in secrets.json (gitignored). Rows use namedtuple_row so `row.col` attribute access works
the same as the old pyodbc code. Session timezone pinned to UTC.
"""
import os
import json
from pathlib import Path
import psycopg
from psycopg.rows import namedtuple_row


def _cfg():
    if os.environ.get("SUPABASE_HOST"):
        return {
            "host": os.environ["SUPABASE_HOST"],
            "port": int(os.environ.get("SUPABASE_PORT", "5432")),
            "dbname": os.environ.get("SUPABASE_DB", "postgres"),
            "user": os.environ["SUPABASE_USER"],
            "password": os.environ["SUPABASE_PASSWORD"],
        }
    cfg = json.loads((Path(__file__).resolve().parent / "secrets.json").read_text()).get("supabase")
    if not cfg:
        raise SystemExit("No Supabase config (env vars or secrets.json 'supabase' block).")
    return cfg


def get_conn(autocommit=False):
    c = _cfg()
    conn = psycopg.connect(
        host=c["host"], port=int(c.get("port", 5432)), dbname=c.get("dbname", "postgres"),
        user=c["user"], password=c["password"], sslmode="require", connect_timeout=15,
        autocommit=autocommit, row_factory=namedtuple_row,
    )
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'UTC'")
    if not autocommit:
        conn.commit()
    return conn
