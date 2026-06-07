"""converge.py - cross-fund convergence (Postgres / Supabase).
Names held by multiple conviction managers (the highest-signal screen the 13F backfill unlocks).
Usage:
  python converge.py                 # latest period, 3+ funds
  python converge.py --min 2         # 2+ funds
  python converge.py --period 2025-12-31
Importable: cross_fund_convergence(conn, period=None, min_funds=3) -> list of rows.
"""
import sys, re
from eb_db import get_conn, dbex


def _ticker_for(cur, cusip):
    if not cusip:
        return None
    # ISIN = country(2) + CUSIP(9) + check(1); CUSIP from 13F is 9 chars; prefix-match
    dbex(cur, "SELECT yf_ticker FROM tbl_eb_universe WHERE isin LIKE %s AND country='US' LIMIT 1",
         "US" + cusip + "%")
    r = cur.fetchone()
    return r.yf_ticker if r else None


def cross_fund_convergence(conn, period=None, min_funds=3, limit=30):
    """Return rows of names held by >=min_funds conviction managers at <period>
    (latest stored if None). Each row: (name, cusip, ticker, funds, total_val, funds_list)."""
    cur = conn.cursor()
    if not period:
        dbex(cur, "SELECT MAX(period) p FROM tbl_eb_sa_13f")
        period = cur.fetchone().p
    if not period:
        return [], None
    dbex(cur, """
        SELECT MIN(name) name, cusip, COUNT(DISTINCT fund) funds, SUM(value) total_val,
               STRING_AGG(DISTINCT fund, ', ') funds_list
        FROM tbl_eb_sa_13f WHERE period=%s AND cusip IS NOT NULL AND cusip<>''
        GROUP BY cusip
        HAVING COUNT(DISTINCT fund) >= %s
        ORDER BY COUNT(DISTINCT fund) DESC, SUM(value) DESC
        LIMIT %s
    """, period, min_funds, limit)
    raw = cur.fetchall()
    out = []
    for r in raw:
        tk = _ticker_for(cur, r.cusip)
        out.append({"name": r.name, "cusip": r.cusip, "ticker": tk,
                    "funds": int(r.funds), "total_val": float(r.total_val or 0),
                    "funds_list": r.funds_list or ""})
    return out, period


def main():
    args = {"min": 3, "period": None}
    for i, a in enumerate(sys.argv[1:]):
        if a == "--min" and i + 2 <= len(sys.argv) - 1:
            args["min"] = int(sys.argv[i + 2])
        elif a == "--period" and i + 2 <= len(sys.argv) - 1:
            args["period"] = sys.argv[i + 2]
    conn = get_conn()
    rows, period = cross_fund_convergence(conn, period=args["period"], min_funds=args["min"])
    print(f"\nCross-fund convergence ({args['min']}+ funds), period {period}")
    if not rows:
        print("  no convergent names found at this threshold")
        return
    print(f"\n  {'name':<32} {'ticker':<8} {'funds':<6} {'$ value':>12}  who")
    for r in rows:
        v = r["total_val"]
        val = f"${v / 1e9:.2f}B" if v >= 1e9 else f"${v / 1e6:.0f}M"
        funds_short = ", ".join(f.replace("Situational Awareness ", "SA ")
                                 .replace("Baillie Gifford", "BG")
                                 .replace("ARK Invest", "ARK")
                                 for f in r["funds_list"].split(", "))
        tk = r["ticker"] or "-"
        print(f"  {r['name'][:32]:<32} {tk:<8} {r['funds']:<6} {val:>12}  {funds_short}")


if __name__ == "__main__":
    main()
