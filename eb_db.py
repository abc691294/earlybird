"""
eb_db.py - Postgres (Supabase) connection for the cloud EarlyBird jobs.

Connection comes from env vars in this order:
  1. already-set env vars (GitHub Actions Secrets in the cloud)
  2. a local .env file (SUPABASE_* lines) - loaded here at import, read IN PLACE (never copied
     or deleted, so no copy-then-rm dance - this is the safe local path)
  3. fallback: a 'supabase' block in secrets.json (legacy; still works if no .env)
Rows use namedtuple_row so `row.col` works. Session timezone pinned to UTC.
"""
import os
import json
from pathlib import Path
import psycopg
from psycopg.rows import namedtuple_row


def _load_dotenv():
    """Load a local .env (KEY=VALUE lines) into os.environ WITHOUT overwriting already-set vars
    (so cloud env vars win). Looks in the repo dir and one level up. Tiny stdlib parser - no
    python-dotenv dependency. Read in place: nothing is copied or deleted."""
    here = Path(__file__).resolve().parent
    for envp in (here / ".env", here.parent / ".env"):
        if not envp.exists():
            continue
        for line in envp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
        break   # first .env found wins


_load_dotenv()


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


def dbex(cur, sql, *params):
    """psycopg execute accepting pyodbc-style positional params (wrapped to a tuple)."""
    cur.execute(sql, params if params else None)
