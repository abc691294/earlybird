"""
enrich_fundamentals.py - pull Yahoo fundamentals for universe tickers into
tbl_eb_fundamentals. This is the data the sector/off-highs filter runs on.

Re-runnable upsert (MERGE on yf_ticker). Sequential by default so we can measure
true per-ticker cost and rate-limit behaviour before scaling up / adding threads.

Usage:
  python enrich_fundamentals.py --limit 100          # random sample of 100
  python enrich_fundamentals.py --all                # everything active
"""
import argparse, time, sys, math
import yfinance as yf
from eb_db import get_conn, dbex

DDL = """
IF OBJECT_ID('dbo.tbl_eb_fundamentals','U') IS NULL
CREATE TABLE dbo.tbl_eb_fundamentals (
  yf_ticker VARCHAR(40) NOT NULL PRIMARY KEY,
  isin VARCHAR(12) NULL,
  as_of DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  price FLOAT NULL,
  currency VARCHAR(8) NULL,
  market_cap BIGINT NULL,
  wk52_low FLOAT NULL,
  wk52_high FLOAT NULL,
  range_pct FLOAT NULL,
  fwd_pe FLOAT NULL,
  trailing_pe FLOAT NULL,
  revenue_growth FLOAT NULL,
  gross_margin FLOAT NULL,
  profit_margin FLOAT NULL,
  target_mean FLOAT NULL,
  total_cash BIGINT NULL,
  total_debt BIGINT NULL,
  sector NVARCHAR(100) NULL,
  industry NVARCHAR(150) NULL,
  summary NVARCHAR(MAX) NULL,
  fetch_ok BIT NOT NULL DEFAULT 0,
  fetch_note NVARCHAR(200) NULL
);
"""

MERGE = """
MERGE dbo.tbl_eb_fundamentals AS t USING #stg AS s ON t.yf_ticker=s.yf_ticker
WHEN MATCHED THEN UPDATE SET t.isin=s.isin, t.as_of=SYSUTCDATETIME(), t.price=s.price,
  t.currency=s.currency, t.market_cap=s.market_cap, t.wk52_low=s.wk52_low, t.wk52_high=s.wk52_high,
  t.range_pct=s.range_pct, t.fwd_pe=s.fwd_pe, t.trailing_pe=s.trailing_pe, t.revenue_growth=s.revenue_growth,
  t.gross_margin=s.gross_margin, t.profit_margin=s.profit_margin, t.target_mean=s.target_mean,
  t.total_cash=s.total_cash, t.total_debt=s.total_debt, t.sector=s.sector, t.industry=s.industry,
  t.summary=s.summary, t.fetch_ok=s.fetch_ok, t.fetch_note=s.fetch_note
WHEN NOT MATCHED THEN INSERT (yf_ticker,isin,price,currency,market_cap,wk52_low,wk52_high,range_pct,
  fwd_pe,trailing_pe,revenue_growth,gross_margin,profit_margin,target_mean,total_cash,total_debt,
  sector,industry,summary,fetch_ok,fetch_note)
  VALUES (s.yf_ticker,s.isin,s.price,s.currency,s.market_cap,s.wk52_low,s.wk52_high,s.range_pct,
  s.fwd_pe,s.trailing_pe,s.revenue_growth,s.gross_margin,s.profit_margin,s.target_mean,s.total_cash,
  s.total_debt,s.sector,s.industry,s.summary,s.fetch_ok,s.fetch_note);
"""


def _num(v):
    try:
        if v is None: return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def fetch(sym, isin):
    note, ok = None, 0
    row = dict(yf_ticker=sym, isin=isin, price=None, currency=None, market_cap=None,
               wk52_low=None, wk52_high=None, range_pct=None, fwd_pe=None, trailing_pe=None,
               revenue_growth=None, gross_margin=None, profit_margin=None, target_mean=None,
               total_cash=None, total_debt=None, sector=None, industry=None, summary=None,
               fetch_ok=0, fetch_note=None)
    try:
        info = yf.Ticker(sym).info or {}
        price = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
        lo, hi = _num(info.get("fiftyTwoWeekLow")), _num(info.get("fiftyTwoWeekHigh"))
        row.update(
            price=price, currency=info.get("currency"),
            market_cap=int(info["marketCap"]) if _num(info.get("marketCap")) else None,
            wk52_low=lo, wk52_high=hi,
            range_pct=round((price - lo) / (hi - lo) * 100, 2) if (price and lo is not None and hi and hi > lo) else None,
            fwd_pe=_num(info.get("forwardPE")), trailing_pe=_num(info.get("trailingPE")),
            revenue_growth=_num(info.get("revenueGrowth")), gross_margin=_num(info.get("grossMargins")),
            profit_margin=_num(info.get("profitMargins")), target_mean=_num(info.get("targetMeanPrice")),
            total_cash=int(info["totalCash"]) if _num(info.get("totalCash")) else None,
            total_debt=int(info["totalDebt"]) if _num(info.get("totalDebt")) else None,
            sector=info.get("sector"), industry=info.get("industry"),
            summary=(info.get("longBusinessSummary") or None),
        )
        if price or row["summary"] or row["market_cap"]:
            ok = 1
        else:
            note = "empty info"
    except Exception as e:
        msg = str(e)
        note = ("RATE LIMIT" if ("429" in msg or "Too Many Requests" in msg.lower()) else msg[:180])
    row["fetch_ok"], row["fetch_note"] = ok, note
    return row


COLS = ["yf_ticker","isin","price","currency","market_cap","wk52_low","wk52_high","range_pct",
        "fwd_pe","trailing_pe","revenue_growth","gross_margin","profit_margin","target_mean",
        "total_cash","total_debt","sector","industry","summary","fetch_ok","fetch_note"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    conn = get_conn(); cur = conn.cursor()
    if args.all:
        dbex(cur, "SELECT yf_ticker, isin FROM tbl_eb_universe WHERE active=true")
    else:
        dbex(cur, "SELECT yf_ticker, isin FROM tbl_eb_universe WHERE active=true ORDER BY random() LIMIT %s", args.limit)
    targets = [(r.yf_ticker, r.isin) for r in cur.fetchall()]
    print(f"pulling {len(targets)} tickers...", flush=True)

    _set = ", ".join(f"{c}=EXCLUDED.{c}" for c in COLS if c != "yf_ticker") + ", as_of=now()"
    UPSERT = (f"INSERT INTO tbl_eb_fundamentals ({','.join(COLS)}, as_of) "
              f"VALUES ({','.join(['%s']*len(COLS))}, now()) "
              f"ON CONFLICT (yf_ticker) DO UPDATE SET {_set}")
    _fk = COLS.index("fetch_ok")
    def flush(batch):
        if not batch: return
        rows = []
        for r in batch:
            row = [r[c] for c in COLS]
            row[_fk] = bool(row[_fk])   # bit -> boolean
            rows.append(row)
        cur.executemany(UPSERT, rows); conn.commit()

    t0 = time.time(); ok = 0; rate = 0; batch = []
    for i, (sym, isin) in enumerate(targets, 1):
        r = fetch(sym, isin)
        batch.append(r); ok += r["fetch_ok"]
        if r["fetch_note"] == "RATE LIMIT": rate += 1
        if len(batch) >= 500:
            flush(batch); batch = []
        if i % 100 == 0:
            el = time.time() - t0
            print(f"  {i}/{len(targets)} | {ok} ok | {rate} rate-limited | {el/60:.1f}min ({el/i:.2f}s/tk)", flush=True)
    flush(batch)

    el = time.time() - t0
    print(f"\nDONE {len(targets)} tickers in {el/60:.1f} min = {el/max(len(targets),1):.2f}s/ticker")
    print(f"  ok={ok} ({100*ok//max(len(targets),1)}%) | rate-limited={rate} | failed={len(targets)-ok}")
    dbex(cur, "SELECT COUNT(*) n, SUM(fetch_ok::int) ok FROM tbl_eb_fundamentals")
    n, okt = cur.fetchone(); print(f"  table now: {n} rows, {okt} ok")
    conn.close()


if __name__ == "__main__":
    main()
