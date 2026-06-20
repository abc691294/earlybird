"""
trail_pullback.py - keep the buy-the-dip alert floor tracking a RISING leader.

A static PRICE_BELOW floor goes stale on a name that keeps climbing: if you set "alert below
51" and the stock runs to 80, a genuine 15% pullback (to ~68) never trips the old floor. So
for held leaders we want to add to on a dip, the floor must trail UP with the price.

Opt-in: put the marker 'TRAIL_PULLBACK[:pct]' in the watchlist triggers field (default 15%).
Each daily run this recomputes PRICE_BELOW = recent_high * (1 - pct/100) and RATCHETS IT UP
only - never down - so a real pullback still triggers, but the level keeps pace as the stock
rises. Names with a plain fixed PRICE_BELOW (a deliberate washout/entry plan, e.g. LAES/MRAM)
are left untouched - only TRAIL_PULLBACK names move.

Runs in the daily job, before watch_alert.py (so the alert reads the fresh floor).
"""
import re
import yfinance as yf
from eb_db import get_conn, dbex

_TRAIL = re.compile(r"TRAIL_PULLBACK(?::\s*([0-9]+(?:\.[0-9]+)?))?", re.I)
_BELOW = re.compile(r"PRICE_BELOW:\s*\$?([0-9]+(?:\.[0-9]+)?)", re.I)
_DEFAULT_PCT = 15.0
_HIGH_WINDOW = "3mo"     # recent high = highest close over this window


def _recent_high(sym):
    try:
        c = yf.Ticker(sym).history(period=_HIGH_WINDOW, auto_adjust=True)["Close"].dropna()
        return float(c.max()) if len(c) else None
    except Exception:
        return None


def main():
    conn = get_conn()
    cur = conn.cursor()
    dbex(cur, "SELECT sym, triggers FROM tbl_eb_watchlist WHERE active=true AND triggers ILIKE '%TRAIL_PULLBACK%'")
    rows = cur.fetchall()
    if not rows:
        print("trail_pullback: no TRAIL_PULLBACK names")
        return
    moved = 0
    for r in rows:
        m = _TRAIL.search(r.triggers or "")
        pct = float(m.group(1)) if (m and m.group(1)) else _DEFAULT_PCT
        hi = _recent_high(r.sym)
        if not hi:
            print(f"  {r.sym}: no price data, skipped")
            continue
        new_floor = round(hi * (1 - pct / 100), 2)
        cur_m = _BELOW.search(r.triggers or "")
        cur_floor = float(cur_m.group(1)) if cur_m else None
        # ratchet UP only: never lower the floor (a real dip must still be able to trip it)
        if cur_floor is not None and new_floor <= cur_floor:
            print(f"  {r.sym}: floor {cur_floor} held (recompute {new_floor} not higher)")
            continue
        if cur_floor is not None:
            new_trig = _BELOW.sub(f"PRICE_BELOW: {new_floor}", r.triggers)
        else:
            new_trig = (r.triggers or "").rstrip() + f"  PRICE_BELOW: {new_floor}"
        dbex(cur, "UPDATE tbl_eb_watchlist SET triggers=%s, updated_on=now() WHERE sym=%s", new_trig, r.sym)
        moved += 1
        print(f"  {r.sym}: floor -> {new_floor} ({pct:.0f}% below recent high {hi:.1f})")
    conn.commit()
    print(f"trail_pullback: {moved} floor(s) ratcheted up")
    conn.close()


if __name__ == "__main__":
    main()
