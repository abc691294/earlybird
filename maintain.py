"""
maintain.py - the self-maintaining fundamentals loop.

Each run picks the TOP 10 names most worth (re)checking, fetches them, and updates a
last_checked stamp on every attempt. The rule you asked for:
  - 404 / not-found / delisted  -> mark `dead=true`, NEVER check again (stops wasting calls)
  - any OTHER failure (rate-limit, timeout, transient) -> leave in the loop to retry later
  - success -> write the data (only on success; never overwrite good data with NULLs)

"Top 10" priority order (most valuable first):
  1. names we HOLD or WATCH (must always be fresh)
  2. strong-fit pool names (the screen's core)
  3. anything else still missing data
within each band, stalest-checked first (so coverage spreads evenly, nothing is starved).

Run on a schedule (e.g. hourly) - it self-heals coverage a little each time, skips the dead,
and keeps the held/watched names current. Cheap and bounded: 10 names per run.
"""
import datetime as dt
from eb_db import get_conn, dbex
from reenrich import fetch, UPSERT, _is_rate_limit, PACE_FAST
import time

BATCH = 10
# a 404-style note means the ticker is unresolvable - mark dead, stop checking it
_DEAD_NOTE = ("not found", "404", "no data", "delisted", "no timezone", "possibly delisted")


def _is_dead_note(note):
    return bool(note) and any(k in note.lower() for k in _DEAD_NOTE)


def pick(conn, n=BATCH):
    """The top N names most worth checking now: held/watched first, then strong-fit pool,
    then the rest still missing data - stalest last_checked first within each band.
    Excludes anything already marked dead."""
    cur = conn.cursor()
    dbex(cur, """
        SELECT u.yf_ticker
        FROM tbl_eb_universe u
        JOIN tbl_eb_fundamentals f ON f.yf_ticker = u.yf_ticker
        WHERE u.active AND f.dead = false
          AND (f.market_cap IS NULL OR f.market_cap = 0          -- still needs data, OR
               OR EXISTS (SELECT 1 FROM tbl_eb_watchlist w        -- held/watched: refresh even if filled
                          WHERE w.sym = u.yf_ticker AND w.active))
        ORDER BY
          -- band 1: held/watched
          (EXISTS (SELECT 1 FROM tbl_eb_watchlist w WHERE w.sym = u.yf_ticker AND w.active)) DESC,
          -- band 2: strong-fit pool
          (EXISTS (SELECT 1 FROM tbl_eb_pool p WHERE p.yf_ticker = u.yf_ticker AND p.fit = 'strong')) DESC,
          f.last_checked ASC NULLS FIRST                          -- stalest first
        LIMIT %s""", n)
    return [r.yf_ticker for r in cur.fetchall()]


def main():
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else BATCH
    conn = get_conn(); cur = conn.cursor()
    syms = pick(conn, n)
    if not syms:
        print("maintain: nothing to check (all enriched or dead)")
        return
    now = dt.datetime.now(dt.timezone.utc)
    ok = dead = retry = 0
    for sym in syms:
        row = fetch(sym)
        if row[18]:                                   # fetch_ok -> write the data
            cur.execute(UPSERT, row)
            dbex(cur, "UPDATE tbl_eb_fundamentals SET last_checked=%s WHERE yf_ticker=%s", now, sym)
            ok += 1
        else:
            note = row[19] or ""
            if _is_dead_note(note) and not _is_rate_limit(note):
                # 404 / unresolvable -> mark dead, never check again
                dbex(cur, "UPDATE tbl_eb_fundamentals SET dead=true, last_checked=%s, fetch_note=%s WHERE yf_ticker=%s",
                     now, note[:200], sym)
                dead += 1
            else:
                # transient (rate-limit/timeout/other) -> stamp checked, leave in the loop
                dbex(cur, "UPDATE tbl_eb_fundamentals SET last_checked=%s, fetch_note=%s WHERE yf_ticker=%s",
                     now, note[:200], sym)
                retry += 1
        conn.commit()
        time.sleep(PACE_FAST)
    print(f"maintain: checked {len(syms)} | {ok} updated, {dead} newly-dead (won't recheck), {retry} kept to retry")
    conn.close()


if __name__ == "__main__":
    main()
