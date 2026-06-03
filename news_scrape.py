"""
news_scrape.py - Tier 2 news: per-ticker headlines for the strong pool (YF, free).

Pulls yfinance .news for every strong-tier name in fn_eb_screen, flags items whose
title looks like a real catalyst (contract/award/offtake/stake/export-control...),
and upserts into tbl_eb_news (deduped on a per-item key) so re-runs only add new.

Catalyst flag is a HINT, not a gate. Tier 1 (industry-RSS rotation feed) is backlog.

Usage:  python news_scrape.py            # strong pool
        python news_scrape.py --all      # every active universe name (slow)
"""
import sys, time, re, datetime as dt
import yfinance as yf
from eb_db import get_conn, dbex

# We want BUSINESS EVENTS (new products, contracts/deals, partnerships, awards, M&A,
# gov/trigger), NOT analyst opinion or macro. Each catalyst is tagged by type.
CATALYST_TYPES = {
 "contract": ["contract", "awarded contract", "contract award", "awarded $", "task order",
              "offtake", "supply agreement", "agreement with", "purchase order", "order from",
              "order for", "letter of intent", "secures", "signs ", "wins ", "selected for",
              "selected by", "booking"],
 "product":  ["launches", "unveils", "introduces", "new product", "rollout", "design win",
              "first commercial", "begins production", "first production", "ramp", "go-live"],
 "partnership": ["partnership", "partners with", "to partner", "collaboration", "joint venture",
                 "teams with", "strategic agreement"],
 "m&a": ["acquisi", "to acquire", "acquires", "merger", " stake in"],
 "gov/trigger": ["pentagon", "department of defense", "department of war", "department of energy",
                 "defense production", "government investment", "grant", "subsid", "export control",
                 "sanction", "tariff", "national security"],
 "milestone": ["milestone", "breakthrough", "approval", "certification", "qualifies", "first revenue"],
}
# suppress: commentary templates AND analyst-opinion (not company events)
NOISE = ["valuation", "a look at", "vs other", "rundown", "should you buy", "looks very risky",
         "ideal long-term", "is it a buy", "top semiconductor", "deep dive",
         "price target", "upgrade", "downgrade", "initiates coverage", "reiterates",
         "earnings call", "earnings transcript", "earnings highlights"]

DDL = """
IF OBJECT_ID('tbl_eb_news','U') IS NULL
CREATE TABLE tbl_eb_news (
  id INT IDENTITY PRIMARY KEY,
  yf_ticker VARCHAR(40) NOT NULL,
  news_key VARCHAR(200) NOT NULL,
  title NVARCHAR(400) NULL,
  publisher NVARCHAR(120) NULL,
  url NVARCHAR(600) NULL,
  published DATETIME2 NULL,
  catalyst BIT NOT NULL DEFAULT 0,
  catalyst_terms NVARCHAR(200) NULL,
  catalyst_type NVARCHAR(20) NULL,
  fetched_on DATETIME2 NOT NULL DEFAULT now(),
  CONSTRAINT UQ_eb_news UNIQUE (yf_ticker, news_key)
);
"""

MERGE = """
INSERT INTO tbl_eb_news (yf_ticker,news_key,title,publisher,url,published,catalyst,catalyst_terms,catalyst_type)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
  ON CONFLICT (yf_ticker,news_key) DO NOTHING
"""


def parse(it):
    c = it.get("content") if isinstance(it.get("content"), dict) else None
    if c:
        title = c.get("title")
        url = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
        pub = c.get("pubDate") or c.get("displayTime")
        publisher = (c.get("provider") or {}).get("displayName")
        nid = it.get("id") or c.get("id")
    else:
        title = it.get("title"); url = it.get("link")
        publisher = it.get("publisher"); nid = it.get("uuid")
        ts = it.get("providerPublishTime")
        pub = dt.datetime.utcfromtimestamp(ts).isoformat() if ts else None
    published = None
    if pub:
        try:
            published = dt.datetime.fromisoformat(str(pub).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            published = None
    key = (nid or url or title or "")[:200]
    return title, publisher, url, published, key


_SUFFIX = re.compile(r"\b(inc|ltd|limited|corp|corporation|plc|holdings?|group|technolog\w*|"
                     r"co|sa|ag|nv|se|the|company|systems?|industries|international)\b")

def _name_token(name):
    """Most distinctive word of the company name (>=4 chars), for headline matching."""
    if not name:
        return None
    w = _SUFFIX.sub(" ", name.lower())
    words = [x for x in re.findall(r"[a-z0-9]+", w) if len(x) >= 4]
    return words[0] if words else None

def _ticker_in_title(title, ticker):
    base = (ticker or "").split(".")[0].upper()
    if len(base) < 2:
        return False
    return re.search(r"(?<![A-Z])" + re.escape(base) + r"(?![A-Z])", title or "") is not None

def flag(title, name=None, ticker=None):
    """(flag, matched_terms, catalyst_type). Requires the company itself in the headline
    (kills peer cross-tags); fires only on BUSINESS-EVENT types, suppresses analyst/commentary."""
    t = (title or "").lower()
    if any(n in t for n in NOISE):
        return 0, None, None
    if name is not None or ticker is not None:
        tok = _name_token(name)
        if not ((tok and tok in t) or _ticker_in_title(title, ticker)):
            return 0, None, None  # article isn't about THIS company
    matched, typ = [], None
    for ty, terms in CATALYST_TYPES.items():
        for x in terms:
            if x in t:
                matched.append(x.strip())
                if typ is None:
                    typ = ty
    return (1 if matched else 0), (",".join(sorted(set(matched)))[:200] or None), typ


def main():
    allmode = "--all" in sys.argv
    conn = get_conn(); cur = conn.cursor()
    if allmode:
        dbex(cur, "SELECT yf_ticker FROM tbl_eb_universe WHERE active=true")
    else:
        dbex(cur, "SELECT DISTINCT yf_ticker FROM tbl_eb_pool")  # whole pool (strong + medium)
    tickers = [r.yf_ticker for r in cur.fetchall()]
    dbex(cur, "SELECT yf_ticker, name FROM tbl_eb_universe")
    names = {r.yf_ticker: r.name for r in cur.fetchall()}
    print(f"news for {len(tickers)} tickers...", flush=True)

    t0 = time.time(); items = 0; cats = 0; covered = 0
    for i, sym in enumerate(tickers, 1):
        try:
            news = yf.Ticker(sym).news or []
        except Exception:
            news = []
        if news: covered += 1
        for it in news:
            title, publisher, url, published, key = parse(it)
            if not key: continue
            cflag, terms, ctype = flag(title, names.get(sym), sym)
            dbex(cur, MERGE, sym, key, (title or "")[:400], (publisher or "")[:120],
                        (url or "")[:600], published, bool(cflag), terms, ctype)
            items += 1; cats += cflag
        if i % 50 == 0:
            conn.commit()
            print(f"  {i}/{len(tickers)} | {covered} with news | {time.time()-t0:.0f}s", flush=True)
    conn.commit()

    print(f"\nDONE {len(tickers)} tickers in {(time.time()-t0)/60:.1f} min")
    print(f"  {covered}/{len(tickers)} had news ({100*covered//max(len(tickers),1)}%)")
    dbex(cur, "SELECT COUNT(*) n, SUM(catalyst::int) c FROM tbl_eb_news")
    n, c = cur.fetchone(); print(f"  tbl_eb_news: {n} items total, {c} catalyst-flagged")
    conn.close()


if __name__ == "__main__":
    main()
