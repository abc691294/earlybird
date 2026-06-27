"""
inbound.py - process tickers Claude Trades (the price-action/growth engine) pushed into
tbl_eb_inbound, and auto-research them into the EarlyBird watchlist.

The two engines are independent screens. Claude Trades surfaces a name on PRICE ACTION
(clean multi-horizon trend x steep slope = growth_score). EarlyBird judges whether it is
ON-BRIEF future-tech, where it sits thematically, and arms a pullback alert. A name passing
BOTH is a stronger signal than either alone - cross-engine agreement RAISES conviction;
disagreement (CT likes the trend but it is off-brief) is itself a useful flag.

What this does per new inbound ticker (MECHANICAL onboarding only - it does NOT fabricate a
verified catalyst or a hand-judged conviction; those happen when the user/Claude confirms):
  - looks it up in the pool (is it on-brief / what theme + fit)
  - sets a conviction tier from the CROSS-ENGINE picture:
        on-brief (pooled) + strong CT growth_score -> 'high'   (both engines agree)
        on-brief, weaker score                     -> 'watch'
        NOT on-brief (CT trend only, no EB theme)  -> left OFF the watchlist, flagged offbrief
  - arms a trailing pullback alert (using the name's own recent high)
  - writes the watchlist row with provenance in the note (source + growth_score + portfolio)
  - records the verdict back on the inbound row, so CT (and the brief) can see what EB concluded

Run daily, after the screen/pool refresh. The brief lists what was auto-researched, flagged
'confirm' - the user greenlights / Claude does the deep catalyst pass on confirm.
"""
import datetime as dt
from eb_db import get_conn, dbex
from supply import chokepoints  # noqa: F401  (available for future chokepoint tagging)

HIGH_SCORE = 3.0          # CT growth_score at/above this + on-brief = both engines strongly agree


def _recent_high_floor(sym, pct=18):
    """Trailing pullback floor = pct below the recent (3mo) high. Best-effort; None if no data."""
    try:
        import yfinance as yf
        c = yf.Ticker(sym).history(period="3mo", auto_adjust=True)["Close"].dropna()
        if len(c) < 10:
            return None
        return round(float(c.max()) * (1 - pct / 100), 2)
    except Exception:
        return None


def process(conn):
    cur = conn.cursor()
    dbex(cur, """SELECT id, yf_ticker, source, portfolio, growth_score, sector_hint
                 FROM tbl_eb_inbound WHERE status='new' ORDER BY growth_score DESC NULLS LAST""")
    rows = cur.fetchall()
    if not rows:
        print("inbound: nothing new")
        return []
    done = []
    for r in rows:
        sym = r.yf_ticker
        # already actively watched? -> dup, just note the CT corroboration and move on
        dbex(cur, "SELECT priority FROM tbl_eb_watchlist WHERE sym=%s AND active=true", sym)
        existing = cur.fetchone()

        # on-brief? = does it have a pool theme match (the screen's verdict)
        dbex(cur, """SELECT sector, fit FROM tbl_eb_pool WHERE yf_ticker=%s
                     ORDER BY CASE fit WHEN 'strong' THEN 0 ELSE 1 END LIMIT 1""", sym)
        pool = cur.fetchone()
        excluded = False
        dbex(cur, "SELECT fn_eb_excluded(%s) e", sym)
        ex = cur.fetchone()
        excluded = bool(ex and ex.e)

        score = r.growth_score or 0
        prov = (f"Flagged by Claude Trades (price-action growth_score {score:.1f}"
                + (f", Portfolio {r.portfolio}" if r.portfolio else "") + ").")

        if excluded:
            verdict = "off-brief (on the exclusion list) - a trade for the CT portfolios, not EarlyBird."
            _set_status(cur, r.id, "offbrief", verdict)
            done.append((sym, "offbrief", verdict))
            continue

        if not pool:
            # CT likes the trend but EarlyBird finds no theme - the useful DISAGREEMENT flag.
            verdict = ("CT likes the price trend but it is NOT on-brief future-tech (no EarlyBird "
                       "theme match). One for the trading portfolios, not the EarlyBird sleeve - unless "
                       "a theme keyword is missing (check the summary).")
            _set_status(cur, r.id, "offbrief", verdict)
            done.append((sym, "offbrief", verdict))
            continue

        # on-brief. Conviction from cross-engine agreement.
        on_brief_strong = pool.fit == "strong"
        tier = "high" if (on_brief_strong and score >= HIGH_SCORE) else "watch"
        agree = ("BOTH engines agree (price-action strong AND on-brief)" if tier == "high"
                 else "on-brief, moderate price-action score")
        why = (f"{prov} EarlyBird: {pool.fit}-fit {pool.sector}. {agree}. "
               f"AUTO-RESEARCHED (mechanical) - conviction set from the cross-engine picture; "
               f"catalyst + final conviction need a confirm pass. Sized per playbook.")
        floor = _recent_high_floor(sym)
        trig = f"TRAIL_PULLBACK: 18  PRICE_BELOW: {floor}" if floor else None

        if existing:
            verdict = (f"already watched (priority {existing.priority}); CT corroborates on price action "
                       f"(growth_score {score:.1f}).")
            _set_status(cur, r.id, "dup", verdict)
            done.append((sym, "dup", verdict))
            continue

        dbex(cur, """INSERT INTO tbl_eb_watchlist
              (sym, name, sector, kind, priority, held, noted, why, triggers, active, updated_on)
              VALUES (%s, %s, %s, 'supplier', %s, false, CURRENT_DATE, %s, %s, true, now())
              ON CONFLICT (sym) DO UPDATE SET priority=EXCLUDED.priority, why=EXCLUDED.why,
                triggers=EXCLUDED.triggers, active=true, updated_on=now()""",
              sym, sym, pool.sector, tier, why, trig)
        verdict = f"on-brief ({pool.fit} {pool.sector}); auto-watchlisted tier={tier}. CONFIRM."
        _set_status(cur, r.id, "researched", verdict)
        done.append((sym, "researched:" + tier, verdict))

    conn.commit()
    print(f"inbound: processed {len(done)} - "
          + ", ".join(f"{s} [{st}]" for s, st, _ in done))
    return done


def _set_status(cur, inbound_id, status, verdict):
    dbex(cur, "UPDATE tbl_eb_inbound SET status=%s, eb_verdict=%s, researched_on=now() WHERE id=%s",
         status, verdict[:300], inbound_id)


def main():
    conn = get_conn()
    process(conn)
    conn.close()


if __name__ == "__main__":
    main()
