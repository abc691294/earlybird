"""
wave_dips.py - daily pullback-entry scan.

Takes the big strong-fit names (established leaders, any direction) and emails
the ones that have pulled back to a Wave Buy or Strong-Buy on the WEEKLY or DAILY timeframe.
The point: get told when a leader dips to a buyable point, instead of watching them by hand.
Reuses the WaveTrend calc (same as the Trump alert), so no TradingView app is needed.
"""
import html
import yfinance as yf
from eb_db import get_conn
from trump_news import send_alert

MIN_CAP = 5_000_000_000     # big names only
WEEKLY_RECENT_BARS = 6      # weekly buy must be within ~6 weeks
DAILY_RECENT_BARS = 10      # daily buy must be within ~10 trading days


def candidates(conn):
    cur = conn.cursor()
    cur.execute("""SELECT p.yf_ticker, MIN(p.sector) sector, MAX(p.market_cap) cap, MAX(m.mv_6m) mv6
        FROM tbl_eb_pool p LEFT JOIN tbl_eb_moves m ON m.yf_ticker=p.yf_ticker
        WHERE p.fit='strong' AND p.market_cap>=%s AND p.yf_ticker NOT LIKE '%%.%%'
        GROUP BY p.yf_ticker ORDER BY MAX(p.market_cap) DESC""", (MIN_CAP,))
    return cur.fetchall()


def _wavetrend(df):
    src = (df["High"] + df["Low"] + df["Close"]) / 3
    esa = src.ewm(span=9, adjust=False).mean()
    de = (src - esa).abs().ewm(span=9, adjust=False).mean()
    ci = (src - esa) / (0.015 * de)
    wt1 = ci.ewm(span=12, adjust=False).mean()
    wt2 = wt1.rolling(3).mean()
    return wt1, wt2


def live_buy(df, recent_bars):
    """Return (zone, signal, date) if the latest Wave signal is a LIVE Buy/Strong-Buy
    (fired recently AND not already run up to overbought), else None."""
    if df is None or len(df.dropna()) < 60:
        return None
    df = df.dropna()
    wt1, wt2 = _wavetrend(df)
    w1 = float(wt1.iloc[-1])
    if w1 >= 60:
        return None                                  # already overbought, buy played out
    cu = (wt1.shift(1) <= wt2.shift(1)) & (wt1 > wt2)
    cd = (wt1.shift(1) >= wt2.shift(1)) & (wt1 < wt2)
    sig, idx = None, None
    for i in range(len(wt1) - 1, max(0, len(wt1) - 300), -1):       # most recent CROSS, either way
        if cu.iloc[i]:
            sig = "Strong Buy" if wt2.iloc[i] <= -80 else ("Buy" if wt2.iloc[i] <= -40 else "up")
            idx = i; break
        if cd.iloc[i]:
            sig = "down"; idx = i; break                            # rolled over -> no live buy
    if sig not in ("Buy", "Strong Buy"):
        return None
    if (len(wt1) - 1 - idx) > recent_bars:
        return None                                  # signal too old
    zone = "oversold" if w1 <= -40 else "neutral"
    return (zone, sig, wt1.index[idx].strftime("%d/%m/%Y"))


def _cell(x):
    return f"<td style='padding:3px 18px 3px 0;font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#1a1a1a'>{x}</td>"


def scan(conn):
    cands = candidates(conn)
    tickers = [r.yf_ticker for r in cands]
    if not tickers:
        return False
    wk = yf.download(tickers, period="3y", interval="1wk", auto_adjust=True, progress=False, group_by="ticker")
    dy = yf.download(tickers, period="1y", interval="1d", auto_adjust=True, progress=False, group_by="ticker")
    multi = len(tickers) > 1
    hits = []
    for r in cands:
        t = r.yf_ticker
        try:
            wdf = wk[t] if multi else wk
        except Exception:
            wdf = None
        try:
            ddf = dy[t] if multi else dy
        except Exception:
            ddf = None
        w = live_buy(wdf, WEEKLY_RECENT_BARS) if wdf is not None else None
        d = live_buy(ddf, DAILY_RECENT_BARS) if ddf is not None else None
        if w or d:
            hits.append((t, r.sector, r.cap, float(r.mv6 or 0), w, d))
    if not hits:
        print("wave_dips: no pullback entries today")
        return False
    rows = ""
    for t, sec, cap, mv6, w, d in hits:
        link = (f'<a href="https://finance.yahoo.com/quote/{t}" '
                f'style="color:#1558d6;text-decoration:none">{t}</a>')
        parts = []
        if w:
            parts.append(f"Weekly {w[1]} ({w[2]})")
        if d:
            parts.append(f"Daily {d[1]} ({d[2]})")
        rows += ("<tr>" + _cell(link) + _cell(html.escape((sec or "")[:18])) + _cell(f"{cap/1e9:.0f}B")
                 + _cell(f"6m {mv6:+.0f}%") + _cell(html.escape(" / ".join(parts))) + "</tr>")
    body = ("<div style='font-family:Arial,Helvetica,sans-serif;color:#1a1a1a'>"
            "<p>Big strong-fit names now at a Wave buy (weekly or daily) - "
            "a pullback entry on a leader:</p>"
            f"<table style='border-collapse:collapse'>{rows}</table></div>")
    ok = send_alert("EarlyBird Pullback Entries", body)
    print(f"wave_dips: {len(hits)} pullback entries; emailed={ok}")
    return ok


if __name__ == "__main__":
    scan(get_conn())
