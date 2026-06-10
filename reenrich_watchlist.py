"""
reenrich_watchlist.py - targeted, gently-paced re-fetch of fundamentals for the
watchlist/held names that failed the bulk weekly enrich (Yahoo rate-limiting).

Small batch + a pause between calls = no throttling. Writes straight to Supabase
(the source of truth). Run after a rate-limited weekly enrich to repair the names
that actually matter.
"""
import time
import yfinance as yf
from eb_db import get_conn   # cloud: env-var connection (GitHub Actions secrets)

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


def _n(v):
    return v if isinstance(v, (int, float)) else None


def fetch(sym):
    try:
        i = yf.Ticker(sym).info
    except Exception as ex:
        return (sym, None, None, None, None, None, None, None, None, None, None, None,
                None, None, None, None, None, None, False, str(ex)[:80])
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


def main():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT sym FROM tbl_eb_watchlist WHERE active ORDER BY held DESC, sym")
    syms = [r.sym for r in cur.fetchall()]
    ok = 0
    for n, sym in enumerate(syms, 1):
        row = fetch(sym)
        cur.execute(UPSERT, row)
        conn.commit()
        status = "ok" if row[18] else f"FAIL ({row[19]})"
        print(f"  [{n:>2}/{len(syms)}] {sym:8} {status}")
        if row[18]:
            ok += 1
        time.sleep(1.5)          # gentle pacing - avoids the bulk-run rate limit
    print(f"re-enriched {ok}/{len(syms)} watchlist names")
    conn.close()


if __name__ == "__main__":
    main()
