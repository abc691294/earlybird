"""
pool.py - materialise fn_eb_screen output into tbl_eb_pool (the screening snapshot the
digest + scrapers read). Postgres/Supabase. fn_eb_screen is (re)created from functions.sql
on each run (CREATE OR REPLACE, idempotent), so the cloud job is self-contained.
"""
from pathlib import Path
from eb_db import get_conn

COLS = ("sector,yf_ticker,name,country,gics_sector,market_cap,range_pct,"
        "fwd_pe,revenue_growth,price,fit_score,matched,fit")


def ensure_function(cur):
    fp = Path(__file__).resolve().parent / "functions.sql"
    if fp.exists():
        cur.execute(fp.read_text())


def refresh(conn=None):
    own = conn is None
    conn = conn or get_conn()
    cur = conn.cursor()
    ensure_function(cur)
    cur.execute("TRUNCATE TABLE tbl_eb_pool")
    cur.execute(f"INSERT INTO tbl_eb_pool ({COLS},refreshed_on) "
                f"SELECT {COLS}, now() FROM fn_eb_screen(NULL)")
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM tbl_eb_pool")
    print(f"tbl_eb_pool refreshed: {cur.fetchone()[0]} rows")
    if own:
        conn.close()


if __name__ == "__main__":
    refresh()
