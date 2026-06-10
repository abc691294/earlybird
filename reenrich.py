"""
reenrich.py - paced re-fetch of fundamentals that the bulk weekly enrich could not
get (Yahoo rate-limits enrich_fundamentals.py --all after ~1000 of 10000+ names).

Two modes:
  python reenrich.py watchlist        # just the watchlist/held names (weekly safety net)
  python reenrich.py failed [N]       # up to N names with no good data yet (daily backlog)

The 'failed' mode is the automatic retry: it works the backlog of fetch_ok=false names
a chunk at a time, stalest first, watchlist names prioritised. It paces calls 1.5s apart
and BACKS OFF (stops the run) after a short burst of rate-limit errors, leaving the rest
for the next run. Over enough daily runs the backlog heals itself; thereafter it just
keeps stale rows fresh. Re-running is always safe (idempotent upsert).
"""
import sys
import time
import yfinance as yf
from eb_db import get_conn

CHUNK_DEFAULT = 400          # names per 'failed' run (~10 min at 1.5s pacing)
PACE_SECONDS = 1.5
BACKOFF_AFTER = 5            # consecutive rate-limit errors -> Yahoo is throttling, stop

UPSERT = """
INSERT INTO tbl_eb_fundamentals
  (yf_ticker, as_of, price, currency, market_cap, wk52_low, wk52_high, range_pct,
   fwd_pe, trailing_pe, revenue_growth, gross_margin, profit_margin, target_mean,
   total_cash, total_debt, sector, industry, summary, fetch_ok, fetch_note)
VALUES (%s, now(), %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (yf_ticker) DO UPDATE SET
  as_of=now(), price=EXCLUDED.price, currency=EXCLUDED.currency,
  market_cap=EXCLUDED.market_cap, wk52_low=EXCLUDED.wk52_low, wk52_high=EXCLUDED.wk52_high,
  range_pct=EXCLUDED.range_pct, fwd_pe=EXCLUDED.fwd_pe, trailing_pe=EXCLUDED.trailing_pe,
  revenue_growth=EXCLUDED.revenue_growth, gross_margin=EXCLUDED.gross_margin,
  profit_margin=EXCLUDED.profit_margin, target_mean=EXCLUDED.target_mean,
  total_cash=EXCLUDED.total_cash, total_debt=EXCLUDED.total_debt,
  sector=EXCLUDED.sector, industry=EXCLUDED.industry, summary=EXCLUDED.summary,
  fetch_ok=EXCLUDED.fetch_ok, fetch_note=EXCLUDED.fetch_note
"""

_RATE_LIMIT = ("too many requests", "rate limit")


def _n(v):
    return v if isinstance(v, (int, float)) else None


def fetch(sym):
    try:
        i = yf.Ticker(sym).info
    except Exception as ex:
        return _row(sym, ok=False, note=str(ex)[:80])
    price = i.get("currentPrice") or i.get("regularMarketPrice")
    lo, hi = _n(i.get("fiftyTwoWeekLow")), _n(i.get("fiftyTwoWeekHigh"))
    rng = round((price - lo) / (hi - lo) * 100, 2) if (price and lo is not None and hi and hi > lo) else None
    cap = i.get("marketCap")
    ok = bool(cap or price)
    return (sym, _n(price), i.get("currency"), int(cap) if cap else None, lo, hi, rng,
            _n(i.get("forwardPE")), _n(i.get("trailingPE")), _n(i.get("revenueGrowth")),
            _n(i.get("grossMargins")), _n(i.get("profitMargins")), _n(i.get("targetMeanPrice")),
            int(i["totalCash"]) if _n(i.get("totalCash")) else None,
            int(i["totalDebt"]) if _n(i.get("totalDebt")) else None,
            i.get("sector"), i.get("industry"), (i.get("longBusinessSummary") or "")[:2000] or None,
            ok, None if ok else "no data")


def _row(sym, ok, note):
    return (sym, None, None, None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, ok, note)


def _is_rate_limit(note):
    return bool(note) and any(k in note.lower() for k in _RATE_LIMIT)


def _process(conn, syms, label):
    cur = conn.cursor()
    ok = consec_rl = 0
    done = 0
    for n, sym in enumerate(syms, 1):
        row = fetch(sym)
        cur.execute(UPSERT, row)
        conn.commit()
        done += 1
        if row[18]:                                   # fetch_ok
            ok += 1
            consec_rl = 0
        else:
            note = row[19] or ""
            consec_rl = consec_rl + 1 if _is_rate_limit(note) else 0
            if _is_rate_limit(note) and consec_rl >= BACKOFF_AFTER:
                print(f"  backing off: {consec_rl} consecutive rate-limit errors at {sym}")
                break
        time.sleep(PACE_SECONDS)
    print(f"{label}: {ok}/{done} fetched ok this run")
    return ok, done


def run_watchlist(conn):
    cur = conn.cursor()
    cur.execute("SELECT sym FROM tbl_eb_watchlist WHERE active ORDER BY held DESC, sym")
    return _process(conn, [r.sym for r in cur.fetchall()], "watchlist")


def run_failed(conn, n):
    cur = conn.cursor()
    # backlog of names with no good data. Priority: watchlist first, then the RECOVERABLE
    # rate-limit failures (these will succeed once paced), then other failures, then
    # never-fetched (often unresolvable foreign tickers). Stalest first within each band.
    cur.execute("""
        SELECT u.yf_ticker
        FROM tbl_eb_universe u
        LEFT JOIN tbl_eb_fundamentals f ON f.yf_ticker = u.yf_ticker
        WHERE u.active AND (f.fetch_ok IS NOT TRUE)
        ORDER BY (EXISTS (SELECT 1 FROM tbl_eb_watchlist w
                          WHERE w.sym = u.yf_ticker AND w.active)) DESC,
                 CASE WHEN f.fetch_note ILIKE '%%rate limit%%'
                        OR f.fetch_note ILIKE '%%too many%%' THEN 0
                      WHEN f.yf_ticker IS NULL THEN 2
                      ELSE 1 END,
                 f.as_of ASC NULLS LAST
        LIMIT %s""", (n,))
    syms = [r.yf_ticker for r in cur.fetchall()]
    if not syms:
        print("failed-backlog: nothing left to retry - all names have data")
        return 0, 0
    cur.execute("""SELECT COUNT(*) c FROM tbl_eb_universe u
                   LEFT JOIN tbl_eb_fundamentals f ON f.yf_ticker=u.yf_ticker
                   WHERE u.active AND (f.fetch_ok IS NOT TRUE)""")
    print(f"failed-backlog: {cur.fetchone().c} names still need data; retrying {len(syms)} this run")
    return _process(conn, syms, "failed-backlog")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "watchlist"
    conn = get_conn()
    if mode == "watchlist":
        run_watchlist(conn)
    elif mode == "failed":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else CHUNK_DEFAULT
        run_failed(conn, n)
    else:
        print(f"unknown mode '{mode}' - use 'watchlist' or 'failed [N]'")
    conn.close()


if __name__ == "__main__":
    main()
