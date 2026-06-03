"""
move_scan.py - price-move detector over the pool.

A big move with NO flagged catalyst is itself a signal (it usually means we missed
the catalyst - wrong listing, weak keyword, etc. - like Sivers). Price moves mirror
across all listings, so this works even on thin cross-listings.

Stores 1m/3m/6m % moves in tbl_eb_moves; the daily job runs it, then the
"movers without a catalyst" query is the investigate list.
"""
import time
import yfinance as yf
from eb_db import get_conn, dbex

DDL = """
IF OBJECT_ID('tbl_eb_moves','U') IS NULL
CREATE TABLE tbl_eb_moves (
  yf_ticker VARCHAR(40) NOT NULL PRIMARY KEY,
  price FLOAT NULL, mv_1m FLOAT NULL, mv_3m FLOAT NULL, mv_6m FLOAT NULL,
  as_of DATETIME2 NOT NULL DEFAULT now());
"""
MERGE = """
INSERT INTO tbl_eb_moves (yf_ticker,price,mv_1m,mv_3m,mv_6m,as_of)
  VALUES (%s,%s,%s,%s,%s, now())
  ON CONFLICT (yf_ticker) DO UPDATE SET
    price=EXCLUDED.price, mv_1m=EXCLUDED.mv_1m, mv_3m=EXCLUDED.mv_3m, mv_6m=EXCLUDED.mv_6m, as_of=now()
"""


def pct(h, back):
    try:
        if len(h) > back:
            a, b = float(h["Close"].iloc[-back-1]), float(h["Close"].iloc[-1])
            return round((b/a - 1)*100, 1) if a else None
    except Exception:
        pass
    return None


def main():
    conn = get_conn(); cur = conn.cursor()
    dbex(cur, "SELECT DISTINCT yf_ticker FROM tbl_eb_pool")
    tickers = [r.yf_ticker for r in cur.fetchall()]
    print(f"move scan: {len(tickers)} tickers", flush=True)
    t0 = time.time(); n = 0
    for i, sym in enumerate(tickers, 1):
        try:
            h = yf.Ticker(sym).history(period="6mo")
        except Exception:
            h = None
        if h is None or h.empty:
            continue
        price = float(h["Close"].iloc[-1])
        m1, m3, m6 = pct(h, 21), pct(h, 63), pct(h, len(h)-1)
        dbex(cur, MERGE, sym, price, m1, m3, m6)
        n += 1
        if i % 200 == 0:
            conn.commit(); print(f"  {i}/{len(tickers)} | {time.time()-t0:.0f}s", flush=True)
    conn.commit()
    print(f"\nDONE {n} priced in {(time.time()-t0)/60:.1f} min")
    # the investigate list: big movers with NO business-event catalyst in 30d
    dbex(cur, """
      SELECT m.yf_ticker, p.sector, p.fit, m.mv_1m, m.mv_3m,
        (SELECT COUNT(*) FROM tbl_eb_news n WHERE n.yf_ticker=m.yf_ticker AND n.catalyst=true
          AND n.published>=(now() - interval '30 days')) cat
      FROM tbl_eb_moves m
      JOIN (SELECT yf_ticker, MIN(sector) sector, MAX(fit) fit FROM tbl_eb_pool GROUP BY yf_ticker) p
        ON p.yf_ticker=m.yf_ticker
      WHERE m.mv_3m >= 80 AND (m.price IS NULL OR m.price >= 0.10)  -- guard: drop sub-0.10 penny artifacts
      ORDER BY m.mv_3m DESC LIMIT 20""")
    print("\n=== BIG MOVERS (3m >= 80%) - 'cat=0' = moved but no catalyst found (INVESTIGATE) ===")
    for r in cur.fetchall():
        print(f"  {r.yf_ticker:9} {(r.sector or '')[:16]:16} {r.fit:6} 1m {r.mv_1m or 0:>6.0f}% 3m {r.mv_3m or 0:>7.0f}% | cat={r.cat}")
    conn.close()


if __name__ == "__main__":
    main()
