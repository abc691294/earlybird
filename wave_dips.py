"""
wave_dips.py - daily pullback-entry scan.

Scans two universes together and emails the names now at a Wave Buy/Strong-Buy on the
WEEKLY or DAILY timeframe (a pullback entry on a leader):
  1. our pool - the big strong-fit future-tech names (>=5B);
  2. the NASDAQ-100 - a fixed list of the biggest Nasdaq names, so a dip on a big name is
     never missed even if our keywords have not tagged it (Tesla + biotech members excluded
     per the mandate).
Reuses the WaveTrend calc (same as the Trump alert), so no TradingView app is needed.
The signal uses the MOST RECENT cross either way, so a buy that has rolled over is not flagged.
"""
import html
import yfinance as yf
from eb_db import get_conn
from trump_news import send_alert

MIN_CAP = 5_000_000_000     # big pool names only
WEEKLY_RECENT_BARS = 2      # weekly buy must have JUST turned (this/last week)
DAILY_RECENT_BARS = 5       # daily buy must have JUST turned (within the last week)

# NASDAQ-100 minus Tesla (Musk mandate) and the biotech/healthcare members (mandate).
NAS100 = ("AAPL ABNB ADBE ADI ADP ADSK AEP AMAT AMD AMZN APP ARM ASML AVGO AXON BKNG BKR CCEP "
 "CDNS CDW CEG CHTR CMCSA COST CPRT CRWD CSCO CSGP CSX CTAS CTSH DASH DDOG EA EXC FANG FAST FTNT "
 "GFS GOOG GOOGL HON INTC INTU KDP KHC KLAC LIN LRCX LULU MAR MCHP MDLZ MELI META MNST MRVL MSFT "
 "MU NFLX NVDA NXPI ODFL ON ORLY PANW PAYX PCAR PDD PEP PLTR PYPL QCOM ROP ROST SBUX SNPS TEAM "
 "TMUS TTD TTWO TXN VRSK WBD WDAY XEL ZS").split()


def candidates(conn):
    """Merge the pool's big strong-fit names with the NASDAQ-100, deduped.
    Returns list of (ticker, sector, cap, mv6)."""
    cur = conn.cursor()
    cur.execute("""SELECT p.yf_ticker, MIN(p.sector) sector, MAX(p.market_cap) cap, MAX(m.mv_6m) mv6
        FROM tbl_eb_pool p LEFT JOIN tbl_eb_moves m ON m.yf_ticker=p.yf_ticker
        WHERE p.fit='strong' AND p.market_cap>=%s AND p.yf_ticker NOT LIKE '%%.%%'
        GROUP BY p.yf_ticker""", (MIN_CAP,))
    out = {}
    for r in cur.fetchall():
        out[r.yf_ticker] = (r.yf_ticker, r.sector, r.cap, r.mv6)
    for t in NAS100:
        if t not in out:
            out[t] = (t, "NASDAQ-100", None, None)
    return list(out.values())


def _wavetrend(df):
    src = (df["High"] + df["Low"] + df["Close"]) / 3
    esa = src.ewm(span=9, adjust=False).mean()
    de = (src - esa).abs().ewm(span=9, adjust=False).mean()
    ci = (src - esa) / (0.015 * de)
    wt1 = ci.ewm(span=12, adjust=False).mean()
    wt2 = wt1.rolling(3).mean()
    return wt1, wt2


def live_buy(df, recent_bars):
    """Return (zone, signal, date) if the MOST RECENT cross is a fresh oversold Buy/Strong-Buy
    (recent AND not already run up to overbought), else None. A later down-cross cancels a buy."""
    if df is None or len(df.dropna()) < 60:
        return None
    df = df.dropna()
    wt1, wt2 = _wavetrend(df)
    w1 = float(wt1.iloc[-1])
    if w1 >= 60:
        return None
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
        return None
    zone = "oversold" if w1 <= -40 else "neutral"
    return (zone, sig, wt1.index[idx].strftime("%d/%m/%Y"))


def _cell(x):
    return f"<td style='padding:3px 18px 3px 0;font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#1a1a1a'>{x}</td>"


def scan(conn):
    cands = candidates(conn)
    tickers = [c[0] for c in cands]
    if not tickers:
        return False
    wk = yf.download(tickers, period="3y", interval="1wk", auto_adjust=True, progress=False, group_by="ticker")
    dy = yf.download(tickers, period="1y", interval="1d", auto_adjust=True, progress=False, group_by="ticker")
    multi = len(tickers) > 1
    hits = []
    for ticker, sector, cap, mv6 in cands:
        try:
            wdf = wk[ticker] if multi else wk
        except Exception:
            wdf = None
        try:
            ddf = dy[ticker] if multi else dy
        except Exception:
            ddf = None
        w = live_buy(wdf, WEEKLY_RECENT_BARS) if wdf is not None else None
        d = live_buy(ddf, DAILY_RECENT_BARS) if ddf is not None else None
        if w or d:
            score = (4 if w and w[1] == "Strong Buy" else 3 if w else 0) + (2 if d and d[1] == "Strong Buy" else 1 if d else 0)
            hits.append((score, ticker, sector, cap, mv6, w, d))
    if not hits:
        print("wave_dips: no pullback entries today")
        return False
    rows = ""
    for _, t, sec, cap, mv6, w, d in sorted(hits, key=lambda h: -h[0]):
        link = (f'<a href="https://finance.yahoo.com/quote/{t}" '
                f'style="color:#1558d6;text-decoration:none">{t}</a>')
        parts = []
        if w:
            parts.append(f"Weekly {w[1]} ({w[2]})")
        if d:
            parts.append(f"Daily {d[1]} ({d[2]})")
        capx = f"{cap/1e9:.0f}B" if cap else "-"
        mvx = f"6m {mv6:+.0f}%" if mv6 is not None else ""
        rows += ("<tr>" + _cell(link) + _cell(html.escape((sec or "")[:18])) + _cell(capx)
                 + _cell(mvx) + _cell(html.escape(" / ".join(parts))) + "</tr>")
    body = ("<div style='font-family:Arial,Helvetica,sans-serif;color:#1a1a1a'>"
            "<p>Names now at a Wave buy (weekly or daily) - a pullback entry on a leader. "
            "Covers our strong-fit pool and the NASDAQ-100:</p>"
            f"<table style='border-collapse:collapse'>{rows}</table></div>")
    ok = send_alert("EarlyBird Pullback Entries", body)
    print(f"wave_dips: {len(hits)} pullback entries; emailed={ok}")
    return ok


if __name__ == "__main__":
    scan(get_conn())
