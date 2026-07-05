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
from reenrich import fetch_batch, UPSERT, _is_rate_limit, PACE_FAST
import time

BATCH = 10
# a 404-style note means the ticker is unresolvable - mark dead, stop checking it.
# "no data" is DELIBERATELY NOT here: reenrich returns "no data" for ANY empty/flaky fetch that
# isn't an explicit rate-limit string, so treating it as dead wiped 8900+ real names (incl AAPL/
# MSFT/NVDA) twice on 05/07/2026 when Yahoo threw a wave of empty responses. Only an EXPLICIT
# unresolvable signal marks a name dead; an ambiguous empty response is transient -> retry.
_DEAD_NOTE = ("not found", "404", "delisted", "no timezone", "possibly delisted")


def _is_dead_note(note):
    return bool(note) and any(k in note.lower() for k in _DEAD_NOTE)


def pick(conn, n=BATCH):
    """GAPS FIRST: clear the whole universe before refreshing anything.
    Priority: (1) names with NO data yet (market_cap NULL/0) - regardless of band; only THEN
    (2) held/watched names that already have data, for a refresh. Within 'gaps', held/watched
    and strong-fit still sort ahead so the most useful gaps fill first, and never-checked
    before stale-checked. Excludes dead tickers."""
    cur = conn.cursor()
    dbex(cur, """
        SELECT u.yf_ticker
        FROM tbl_eb_universe u
        JOIN tbl_eb_fundamentals f ON f.yf_ticker = u.yf_ticker
        WHERE u.active AND f.dead = false
          AND (f.market_cap IS NULL OR f.market_cap = 0          -- a GAP (no data), OR
               OR EXISTS (SELECT 1 FROM tbl_eb_watchlist w        -- held/watched (refresh once gaps done)
                          WHERE w.sym = u.yf_ticker AND w.active))
        ORDER BY
          -- 1) GAPS before refreshes: anything missing data comes first, full stop
          (f.market_cap IS NOT NULL AND f.market_cap > 0) ASC,
          -- 2) within gaps, fill the most useful first: held/watched, then strong-fit pool
          (EXISTS (SELECT 1 FROM tbl_eb_watchlist w WHERE w.sym = u.yf_ticker AND w.active)) DESC,
          (EXISTS (SELECT 1 FROM tbl_eb_pool p WHERE p.yf_ticker = u.yf_ticker AND p.fit = 'strong')) DESC,
          -- 3) never-checked before stale-checked
          f.last_checked ASC NULLS FIRST
        LIMIT %s""", n)
    return [r.yf_ticker for r in cur.fetchall()]


def pick_all(conn):
    """EVERY active, non-dead name - a full-universe refresh (weekly), so enriched names that
    aren't on the watchlist don't go stale. Cheap now that fetch is batched. Stalest first."""
    cur = conn.cursor()
    dbex(cur, """SELECT u.yf_ticker FROM tbl_eb_universe u
                 JOIN tbl_eb_fundamentals f ON f.yf_ticker = u.yf_ticker
                 WHERE u.active AND f.dead = false
                 ORDER BY f.last_checked ASC NULLS FIRST""")
    return [r.yf_ticker for r in cur.fetchall()]


def main():
    import sys
    args = sys.argv[1:]
    full = "--all" in args
    nums = [a for a in args if a.isdigit()]
    n = int(nums[0]) if nums else BATCH
    conn = get_conn(); cur = conn.cursor()
    syms = pick_all(conn) if full else pick(conn, n)
    if full:
        print(f"maintain --all: full-universe refresh of {len(syms)} active names")
    if not syms:
        print("maintain: nothing to check (all enriched or dead)")
        return
    now = dt.datetime.now(dt.timezone.utc)
    ok = dead = retry = 0
    timed_out = []                                    # tickers whose chunk failed even after a retry
    CHUNK = 200                                       # batched yahooquery fetch - hundreds per call
    # ADAPTIVE BACKOFF (root-cause fix 05/07/2026): the full --all run has ~51 chunks. With no
    # backoff at a flat 0.5s pace it exhausted Yahoo, which then returned EMPTY ('no data') for the
    # rest of the run - looking like thousands of dead names. Now: when a chunk comes back mostly
    # empty (a throttle signal, whether flagged rate_limit OR silent 'no data'), slow down; after a
    # few consecutive mostly-empty chunks, STOP the run - the unprocessed names keep their prior data
    # for next time. Better a partial refresh than a hammered API returning garbage.
    pace = 0.5
    empty_streak = 0
    EMPTY_FRAC = 0.6            # a chunk >=60% empty = Yahoo is throttling us
    STOP_AFTER = 3             # consecutive throttled chunks -> stop, don't hammer
    for i in range(0, len(syms), CHUNK):
        chunk = syms[i:i + CHUNK]
        rows = None
        for attempt in (1, 2):                         # one retry on a whole-chunk failure (network timeout)
            try:
                rows, _rl = fetch_batch(chunk)
                break
            except Exception as ex:
                if attempt == 1:
                    print(f"  chunk {i} attempt 1 failed ({str(ex)[:50]}) - retrying")
                    time.sleep(5)
                else:
                    # retry also failed -> do NOT fail the task. Log which tickers, stamp them so
                    # they are re-tried next run, count them, and carry on (run still ends OK).
                    print(f"  chunk {i} timed out after retry - {len(chunk)} names deferred: "
                          + ",".join(chunk))
                    for s in chunk:
                        dbex(cur, "UPDATE tbl_eb_fundamentals SET last_checked=%s, fetch_note=%s WHERE yf_ticker=%s",
                             now, "chunk timeout (deferred to next run)", s)
                    timed_out.extend(chunk)
                    conn.commit()
        if rows is None:                               # both attempts failed - already handled above
            continue
        # throttle detection: how empty was this chunk? (ok=False on a fetch = no data returned)
        n_empty = sum(1 for row in rows if not row[18])
        throttled = rows and (n_empty / len(rows)) >= EMPTY_FRAC
        if throttled:
            empty_streak += 1                           # cumulative count (does NOT reset on a good
            pace = min(pace * 2, 8.0)                   # chunk) - alternating empty/full chunks still
            print(f"  chunk {i}: {n_empty}/{len(rows)} empty - throttled, pace now {pace}s "  # trips the stop
                  f"(throttled {empty_streak}/{STOP_AFTER})")
            if empty_streak >= STOP_AFTER:
                print(f"  STOP: {STOP_AFTER} throttled chunks this run - Yahoo is rate-limiting. "
                      f"Ending run; {len(syms) - i - len(chunk)} names left keep prior data for next run.")
                break
        else:
            pace = max(pace / 2, 0.5)                   # recover pace when Yahoo is happy again
            # note: empty_streak is NOT reset - a run that keeps hitting throttled chunks (even with
            # good ones between) is still being rate-limited overall, and should stop, not thrash.
        for row in rows:
            sym = row[0]
            if row[18]:                               # fetch_ok -> write the data
                cur.execute(UPSERT, row)
                # a PRICE-ONLY row (has a price, but no market_cap AND no industry) is an ETF/fund/
                # thin secondary listing - no company fundamentals exist, so it can never enrich.
                # Mark dead so it stops counting as a gap. CRITICAL: require row[2] (price) to be
                # present - a throttled/failed fetch also has null market_cap+industry, but null
                # PRICE too. Without this price check, a rate-limited run wrongly killed 8500+ real
                # names incl AAPL/MSFT/NVDA (05/07/2026). Price present = real ETF; price null = a
                # failed fetch -> transient, never dead.
                if row[1] and not row[3] and not row[16]:  # price(1) present, no market_cap(3)/industry(16)
                    dbex(cur, "UPDATE tbl_eb_fundamentals SET dead=true, last_checked=%s WHERE yf_ticker=%s", now, sym)
                    dead += 1
                else:
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
        time.sleep(pace)
    to = f", {len(timed_out)} timed-out-deferred" if timed_out else ""
    print(f"maintain: checked {len(syms)} | {ok} updated, {dead} newly-dead (won't recheck), "
          f"{retry} kept to retry{to}  (run OK)")
    conn.close()


if __name__ == "__main__":
    main()
