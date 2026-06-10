"""
suggest.py - the engine-room digest, LOG OUTPUT ONLY (no email).
Per SPEC.md (10/06/2026) the only scheduled email is the weekly brief (brief.py); the
same-day buying-moment alert is watch_alert.py. This digest prints the raw surfacing
view to the daily workflow log for debugging and audit - nothing is sent anywhere.
"""
from eb_db import get_conn, dbex


def digest(conn):
    cur = conn.cursor()

    print("\n=== 1. TRENDING SECTORS (movers + real catalysts, 30d) ===")
    dbex(cur, """
      SELECT p.sector,
        SUM(CASE WHEN m.mv_3m>=40 THEN 1 ELSE 0 END) movers,
        (SELECT COUNT(DISTINCT n.yf_ticker) FROM tbl_eb_news n
          JOIN tbl_eb_pool pp ON pp.yf_ticker=n.yf_ticker AND pp.sector=p.sector
          WHERE n.catalyst=true AND n.published>=(now() - interval '30 days')) cat_names
      FROM tbl_eb_pool p LEFT JOIN tbl_eb_moves m ON m.yf_ticker=p.yf_ticker
      GROUP BY p.sector ORDER BY movers DESC""")
    for r in cur.fetchall():
        print(f"  {r.sector:24} {r.movers:>3} movers | {r.cat_names:>3} names with catalysts")

    print("\n=== 2. MERITED MOVERS - moved + a real business event (OWN / WATCH; emerging first) ===")
    dbex(cur, """
      WITH cat AS (SELECT yf_ticker, MAX(catalyst_type) ctype, MAX(title) ttl FROM tbl_eb_news
                   WHERE catalyst=true AND published>=(now() - interval '30 days') GROUP BY yf_ticker)
      SELECT p.yf_ticker, p.sector, p.fit, p.market_cap, m.mv_3m, c.ctype, LEFT(c.ttl,42) t
      FROM tbl_eb_pool p JOIN tbl_eb_moves m ON m.yf_ticker=p.yf_ticker
      JOIN cat c ON c.yf_ticker=p.yf_ticker
      WHERE m.mv_3m BETWEEN 15 AND 500 AND (m.price IS NULL OR m.price>=0.10)
      ORDER BY CASE WHEN p.fit='strong' THEN 0 ELSE 1 END, p.market_cap ASC LIMIT 16""")
    for r in cur.fetchall():
        cap = f"{r.market_cap/1e6:.0f}M" if r.market_cap and r.market_cap < 1e9 else (f"{r.market_cap/1e9:.1f}B" if r.market_cap else "-")
        print(f"  {r.yf_ticker:8} {(r.sector or '')[:15]:15} {r.fit:6} {cap:>6} 3m{r.mv_3m or 0:>+5.0f}% [{r.ctype or '?':9}] {r.t}")

    print("\n=== 3. PUMP / VERIFY - moved hard, NO catalyst found (caution, or a missed catalyst) ===")
    dbex(cur, """
      SELECT m.yf_ticker, p.sector, p.fit, m.mv_1m, m.mv_3m
      FROM tbl_eb_moves m JOIN (SELECT yf_ticker,MIN(sector) sector,MAX(fit) fit FROM tbl_eb_pool GROUP BY yf_ticker) p
        ON p.yf_ticker=m.yf_ticker
      WHERE m.mv_3m>=80 AND (m.price IS NULL OR m.price>=0.10)
        AND NOT EXISTS (SELECT 1 FROM tbl_eb_news n WHERE n.yf_ticker=m.yf_ticker AND n.catalyst=true
                        AND n.published>=(now() - interval '30 days'))
      ORDER BY m.mv_3m DESC LIMIT 12""")
    for r in cur.fetchall():
        print(f"  {r.yf_ticker:8} {(r.sector or '')[:15]:15} {r.fit:6} 1m{r.mv_1m or 0:>+5.0f}% 3m{r.mv_3m or 0:>+6.0f}%")

    print("\n=== 6. PULLBACKS - strong-fit names DOWN recently (legitimate merited entries) ===")
    dbex(cur, """
      SELECT p.yf_ticker, p.sector, p.fit, p.market_cap, m.mv_1m, m.mv_3m
      FROM tbl_eb_pool p JOIN tbl_eb_moves m ON m.yf_ticker=p.yf_ticker
      WHERE p.fit='strong' AND m.mv_1m <= -12 AND (m.price IS NULL OR m.price>=0.10)
      ORDER BY p.market_cap DESC LIMIT 14""")
    for r in cur.fetchall():
        cap = f"{r.market_cap/1e6:.0f}M" if r.market_cap and r.market_cap < 1e9 else (f"{r.market_cap/1e9:.1f}B" if r.market_cap else "-")
        ctx = "(dip in uptrend)" if (r.mv_3m or 0) > 0 else "(in downtrend)"
        print(f"  {r.yf_ticker:8} {(r.sector or '')[:15]:15} {cap:>6} 1m{r.mv_1m or 0:>+5.0f}% 3m{r.mv_3m or 0:>+5.0f}% {ctx}")

    print("\n=== 7. STALWART DIPS - large-cap merited names on a MODEST pullback (buying-window) ===")
    dbex(cur, """
      SELECT p.yf_ticker, p.sector, p.market_cap, m.mv_1m, m.mv_3m, m.mv_6m
      FROM (SELECT yf_ticker, MIN(sector) sector, MAX(market_cap) market_cap, MAX(fit) fit
            FROM tbl_eb_pool GROUP BY yf_ticker) p
      JOIN tbl_eb_moves m ON m.yf_ticker=p.yf_ticker
      WHERE p.fit='strong' AND p.market_cap >= 10000000000
        AND m.mv_1m <= -6 AND (m.price IS NULL OR m.price>=0.10)
      ORDER BY m.mv_1m ASC LIMIT 14""")
    for r in cur.fetchall():
        cap = f"{r.market_cap/1e9:.0f}B" if r.market_cap else "-"
        ctx = "(dip in uptrend)" if (r.mv_6m or 0) > 0 else "(weak longer trend)"
        print(f"  {r.yf_ticker:8} {(r.sector or '')[:18]:18} {cap:>5} 1m{r.mv_1m or 0:>+5.0f}% 6m{r.mv_6m or 0:>+6.0f}% {ctx}")

    print("\n=== 5. VERY EARLY - strong-fit small-caps, in-theme, NOT yet moved (scattershot) ===")
    dbex(cur, """
      SELECT p.yf_ticker, p.sector, p.market_cap, p.range_pct, m.mv_3m, p.matched
      FROM tbl_eb_pool p LEFT JOIN tbl_eb_moves m ON m.yf_ticker=p.yf_ticker
      WHERE p.fit='strong' AND p.market_cap BETWEEN 30000000 AND 1500000000
        AND (m.mv_3m IS NULL OR m.mv_3m < 30)
      ORDER BY p.market_cap ASC LIMIT 18""")
    for r in cur.fetchall():
        cap = f"{r.market_cap/1e6:.0f}M" if r.market_cap and r.market_cap < 1e9 else (f"{r.market_cap/1e9:.1f}B" if r.market_cap else "-")
        mv = f"{r.mv_3m:+.0f}%" if r.mv_3m is not None else "quiet"
        print(f"  {r.yf_ticker:8} {(r.sector or '')[:18]:18} {cap:>6} rng={r.range_pct or 0:>3.0f}% 3m={mv:>6} ['{r.matched}']")

    print("\n=== 4. KEYWORD REFINE - medium-fit names that moved hard (likely under/mis-tagged) ===")
    dbex(cur, """
      SELECT p.yf_ticker, p.sector, p.matched, m.mv_3m
      FROM tbl_eb_pool p JOIN tbl_eb_moves m ON m.yf_ticker=p.yf_ticker
      WHERE p.fit='medium' AND m.mv_3m>=60 AND (m.price IS NULL OR m.price>=0.10)
      ORDER BY m.mv_3m DESC LIMIT 10""")
    for r in cur.fetchall():
        print(f"  {r.yf_ticker:8} {(r.sector or '')[:15]:15} matched '{r.matched}' 3m{r.mv_3m or 0:>+5.0f}%  -> re-check sector/tier")

    print("\n=== 8. TRUMP POSITIVE MENTIONS - established names Trump praised (last 3d; * = strong-fit) ===")
    dbex(cur, """
      WITH ranked AS (
        SELECT t.matched_ticker, t.source, LEFT(t.title,56) ttl, t.published,
          ROW_NUMBER() OVER (PARTITION BY t.matched_ticker ORDER BY t.published DESC) rn
        FROM tbl_eb_trump_news t
        WHERE t.in_universe=true AND t.sentiment='positive' AND t.published >= (now() - interval '3 days'))
      SELECT r.matched_ticker, r.source, r.ttl,
        (SELECT MAX(p.fit) FROM tbl_eb_pool p WHERE p.yf_ticker=r.matched_ticker) fit,
        (SELECT f.market_cap FROM tbl_eb_fundamentals f WHERE f.yf_ticker=r.matched_ticker) cap,
        CASE WHEN r.ttl ILIKE '%bought%' OR r.ttl ILIKE '%stake%' OR r.ttl ILIKE '%disclos%'
                  OR r.ttl ILIKE '%invest%' OR r.ttl ILIKE '%acquir%' THEN 1 ELSE 0 END is_pos
      FROM ranked r WHERE r.rn=1
      ORDER BY CASE WHEN EXISTS (SELECT 1 FROM tbl_eb_pool p WHERE p.yf_ticker=r.matched_ticker AND p.fit='strong') THEN 0 ELSE 1 END,
               CASE WHEN r.ttl ILIKE '%bought%' OR r.ttl ILIKE '%stake%' OR r.ttl ILIKE '%disclos%'
                         OR r.ttl ILIKE '%invest%' OR r.ttl ILIKE '%acquir%' THEN 0 ELSE 1 END,
               r.published DESC LIMIT 14""")
    rows = cur.fetchall()
    if not rows:
        print("  (no positive established-name mentions in the window)")
    for r in rows:
        star = "*" if r.fit == 'strong' else " "
        tag = "BUY" if r.is_pos else "say"
        cap = f"{r.cap/1e9:.0f}B" if r.cap and r.cap >= 1e9 else (f"{(r.cap or 0)/1e6:.0f}M" if r.cap else "  -")
        print(f"  {star}{r.matched_ticker:7} {cap:>5} [{tag}] {r.ttl}")

    print("\n=== 9. TRUMP POLICY -> BENEFICIARIES (sector movers from his trade actions; ! = he owns it) ===")
    dbex(cur, """
      SELECT DISTINCT s.theme, s.ticker, s.is_holding, LEFT(s.policy_title,46) t
      FROM tbl_eb_policy_signal s
      WHERE s.published >= (now() - interval '5 days')
      ORDER BY s.is_holding DESC, s.theme, s.ticker""")
    rows = cur.fetchall()
    if not rows:
        print("  (no trade/policy actions mapping to beneficiaries in the window)")
    for r in rows:
        own = "!" if r.is_holding else " "
        print(f"  {own}{r.ticker:6} [{(r.theme or '')[:14]:14}] {r.t}")


if __name__ == "__main__":
    c = get_conn(); digest(c); c.close()
