"""
watch_alert.py - the same-day alert. Runs daily; emails ONLY when a watchlist name
hits a buying moment. Two kinds:

  1. The tested pattern (default): the stock's longer trend is still rising
     (price above its 200-day average) AND it has just turned back up from a dip.
  2. A plain price target: for a deliberate trade where you want to buy at a floor,
     put 'PRICE_BELOW: <number>' anywhere in the watchlist triggers field and this
     fires when the price falls to or below that level - no trend check (it is a
     manual trade decision, not the tested method, and is labelled as such).

Everything else is silence. Per-name 7-day cooldown per kind. Plain English, no jargon.
"""
import html
import re
import yfinance as yf
from eb_db import get_conn, dbex
from stock_pumps import send_alert
from wave_dips import live_buy, WEEKLY_RECENT_BARS, DAILY_RECENT_BARS

COOLDOWN_DAYS = 7
P = "margin:0 0 8px;font-family:'Segoe UI',Arial,sans-serif;font-size:10pt;line-height:1.45;color:#1a1a1a"
_PRICE_BELOW = re.compile(r"PRICE_BELOW:\s*\$?([0-9]+(?:\.[0-9]+)?)", re.I)


def _price_target(triggers):
    """The float target from a 'PRICE_BELOW: <n>' note in the triggers field, or None."""
    m = _PRICE_BELOW.search(triggers or "")
    return float(m.group(1)) if m else None


def _uptrend(daily_df):
    """True if the last close is above the 200-day average - the tested health check."""
    closes = daily_df["Close"].dropna()
    if len(closes) < 200:
        return False
    return float(closes.iloc[-1]) > float(closes.rolling(200).mean().iloc[-1])


def main():
    conn = get_conn()
    cur = conn.cursor()
    dbex(cur, """SELECT sym, name, why, triggers FROM tbl_eb_watchlist WHERE active=true""")
    watch = {r.sym: r for r in cur.fetchall()}
    if not watch:
        print("watch_alert: empty watchlist")
        return
    dbex(cur, """SELECT sym, kind FROM tbl_eb_alert_log
                 WHERE kind IN ('buying-moment','price-target')
                   AND sent_on >= now() - interval '%s days'""" % COOLDOWN_DAYS)
    cooled = {(r.sym, r.kind) for r in cur.fetchall()}

    syms = list(watch)
    dy = yf.download(syms, period="2y", interval="1d", auto_adjust=True, progress=False, group_by="ticker")
    wk = yf.download(syms, period="3y", interval="1wk", auto_adjust=True, progress=False, group_by="ticker")
    multi = len(syms) > 1

    hits = []          # tested pattern: (sym, weekly_sig, daily_sig)
    price_hits = []    # plain price target: (sym, last_price, target)
    for sym in syms:
        try:
            ddf = dy[sym] if multi else dy
            wdf = wk[sym] if multi else wk
        except Exception:
            continue
        if ddf is None or ddf.dropna().empty:
            continue
        closes = ddf.dropna()["Close"]
        last = float(closes.iloc[-1])

        # 2. plain price target (independent of trend - it is a manual trade)
        target = _price_target(watch[sym].triggers)
        if target and last <= target and (sym, "price-target") not in cooled:
            price_hits.append((sym, last, target))

        # 1. the tested dip-in-uptrend pattern
        if (sym, "buying-moment") in cooled or not _uptrend(closes):
            continue
        w = live_buy(wdf, WEEKLY_RECENT_BARS) if wdf is not None else None
        d = live_buy(ddf, DAILY_RECENT_BARS)
        if w or d:
            hits.append((sym, w, d))

    if not hits and not price_hits:
        print("watch_alert: no buying moments today")
        conn.close()
        return

    blocks = []
    for sym, w, d in hits:
        r = watch[sym]
        turn = f"weekly turn on {w[2]}" if w else f"daily turn on {d[2]}"
        body = (f"<b>{html.escape(r.name or sym)} ({sym})</b> has hit its buying moment: "
                f"its longer trend is still rising and it has just turned back up from a dip "
                f"({turn}). This is the one entry pattern our testing supports.")
        if r.why:
            body += f"<br><b>Why we watch it:</b> {html.escape(r.why[:240])}"
        if r.triggers:
            body += f"<br><b>Our trigger notes:</b> {html.escape(r.triggers[:240])}"
        blocks.append(f"<p style=\"{P}\">{body}</p>")
    for sym, last, target in price_hits:
        r = watch[sym]
        body = (f"<b>{html.escape(r.name or sym)} ({sym})</b> has reached your price target: "
                f"now ${last:.2f}, at or below your ${target:.2f} level. This is a planned trade "
                f"entry you set, NOT the tested dip-in-uptrend pattern - your own trade plan applies.")
        if r.flags:
            body += f"<br><b>Notes:</b> {html.escape(r.flags[:240])}"
        blocks.append(f"<p style=\"{P}\">{body}</p>")
    blocks.append(f"<p style=\"{P}\"><i>A prompt to look, not an instruction.</i></p>")

    names = ", ".join([h[0] for h in hits] + [p[0] for p in price_hits])
    if send_alert(f"EarlyBird buying moment: {names}", "<div style='max-width:680px'>" + "".join(blocks) + "</div>"):
        for sym, _w, _d in hits:
            dbex(cur, "INSERT INTO tbl_eb_alert_log (sym, kind) VALUES (%s, 'buying-moment')", sym)
        for sym, _l, _t in price_hits:
            dbex(cur, "INSERT INTO tbl_eb_alert_log (sym, kind) VALUES (%s, 'price-target')", sym)
        conn.commit()
        print(f"watch_alert: emailed {names}")
    conn.close()


if __name__ == "__main__":
    main()
