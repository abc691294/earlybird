"""
mention_news.py - daily scan for ESTABLISHED stocks that market-moving FIGURES (beyond Trump)
have spoken POSITIVELY about, or that their companies have struck a partnership/investment with.

Same machinery as trump_news.py (reused by import): Google News discovery, established-name
matching (cap-gated), positive-only sentiment, the SAME freshness rule (a story only counts
if its true publish date is verified and <= 7 days old), Wave grading in the brief.

Figures tracked (config below): Jensen Huang / Nvidia, the hyperscaler CEOs (Nadella, Pichai,
Jassy, Zuckerberg), plus Sam Altman and Lisa Su. Each figure's positive mention of an
established company is a same-day catalyst worth catching before the wider feed.

Stored in tbl_eb_mention_news (its own table, same shape as tbl_eb_trump_news so the brief
reads it identically). Run daily, right after trump_news in the daily workflow.
"""
import datetime as dt
import feedparser
from eb_db import get_conn
# reuse every piece of the Trump pipeline - one source of logic, no duplication
from trump_news import (
    build_matcher, match_company, sentiment, strip_src, is_wrap, pos_near, pub,
    article_date, alt_source_date, dbex,
)

# --- the figures we track. Each: a regex name to require in the headline, plus Google feeds. ---
# 'subject' words are the people/companies; a headline must contain one to count (like is_trump).
FIGURES = {
    "huang":   {"label": "Jensen Huang / Nvidia",
                "subject": r"\b(Jensen Huang|Nvidia|NVIDIA)\b"},
    "nadella": {"label": "Satya Nadella / Microsoft",
                "subject": r"\b(Satya Nadella|Nadella)\b"},
    "pichai":  {"label": "Sundar Pichai / Google",
                "subject": r"\b(Sundar Pichai|Pichai)\b"},
    "jassy":   {"label": "Andy Jassy / Amazon",
                "subject": r"\b(Andy Jassy|Jassy)\b"},
    "zuck":    {"label": "Mark Zuckerberg / Meta",
                "subject": r"\b(Mark Zuckerberg|Zuckerberg)\b"},
    "altman":  {"label": "Sam Altman / OpenAI",
                "subject": r"\b(Sam Altman|Altman)\b"},
    "su":      {"label": "Lisa Su / AMD",
                "subject": r"\b(Lisa Su)\b"},
}

import re as _re
_SUBJECT_RX = {k: _re.compile(v["subject"]) for k, v in FIGURES.items()}

# Curated aliases for the big tech names these figures actually discuss. The Trump matcher's
# cap-gated token map deliberately drops common words (meta/amazon/apple) to avoid collisions
# in political headlines; here the headlines are clean ("X praises Meta"), so a tight, explicit
# alias map is safe and catches the exact catalyst we want. (word boundary, case-insensitive)
_ALIASES = {
    r"\bMeta\b": ("META", "Meta Platforms"),
    r"\bMicrosoft\b": ("MSFT", "Microsoft"),
    r"\bAmazon\b": ("AMZN", "Amazon"),
    r"\b(?:Google|Alphabet)\b": ("GOOGL", "Alphabet"),
    r"\bApple\b": ("AAPL", "Apple"),
    r"\bBroadcom\b": ("AVGO", "Broadcom"),
    r"\bMarvell\b": ("MRVL", "Marvell"),
    r"\bMicron\b": ("MU", "Micron"),
    r"\bCorning\b": ("GLW", "Corning"),
    r"\bCoreWeave\b": ("CRWV", "CoreWeave"),
    r"\bOracle\b": ("ORCL", "Oracle"),
    r"\bPalantir\b": ("PLTR", "Palantir"),
    r"\bTSMC\b": ("TSM", "TSMC"),
    r"\bIntel\b": ("INTC", "Intel"),
    r"\bDell\b": ("DELL", "Dell"),
    r"\bSuper\s?Micro\b": ("SMCI", "Super Micro"),
}
_ALIAS_RX = [(_re.compile(p, _re.I), tk, nm) for p, (tk, nm) in _ALIASES.items()]


# A genuine endorsement/deal verb must sit within 40 chars of the company name - this kills
# "X richer than Zuckerberg" (wealth), "Intel vs AMD vs Nvidia" (comparison), "Modi earns
# praise from Cook and Nadella" (the praise is for someone else), which merely co-mention a
# figure, a company and a positive word without the figure actually backing the company.
_ENDORSE = _re.compile(
    r"\b(prais\w*|tout\w*|hail\w*|back(?:s|ed|ing)|endors\w*|partner\w*|invest\w*|stake|"
    r"deal|supply|buys?|bought|acquir\w*|loves?|picks?|chose|selects?)\b", _re.I)


def alias_match(title, speaker_key):
    """Map a clean 'figure praises/partners Company' headline to a ticker via the curated alias
    map. Requires an endorsement verb NEAR the company name, and skips the speaker's OWN company."""
    own = {"huang": "NVDA", "nadella": "MSFT", "pichai": "GOOGL", "jassy": "AMZN",
           "zuck": "META", "altman": None, "su": "AMD"}.get(speaker_key)
    low = (title or "")
    for rx, tk, nm in _ALIAS_RX:
        if tk == own:
            continue
        m = rx.search(low)
        if not m:
            continue
        seg = low[max(0, m.start() - 40): m.end() + 40]   # endorsement must be next to the company
        if _ENDORSE.search(seg):
            return tk, nm
    return None, None


def _feeds(person):
    """Google News RSS feeds for one figure - praise + partnership/investment patterns."""
    q = person.replace(" ", "%20")
    return [
        f"https://news.google.com/rss/search?q={q}%20(praises%20OR%20touts%20OR%20backs%20OR%20partners%20OR%20partnership)%20when:5d&hl=en-US&gl=US&ceid=US:en",
        f"https://news.google.com/rss/search?q={q}%20(invests%20OR%20stake%20OR%20deal%20OR%20supply%20OR%20%22to%20buy%22)%20(stock%20OR%20shares%20OR%20company)%20when:6d&hl=en-US&gl=US&ceid=US:en",
    ]

# the people whose names drive the discovery feeds (companies caught via the subject regex)
PEOPLE = ["Jensen Huang", "Satya Nadella", "Sundar Pichai", "Andy Jassy",
          "Mark Zuckerberg", "Sam Altman", "Lisa Su"]

DDL = """
CREATE TABLE IF NOT EXISTS tbl_eb_mention_news (
  id SERIAL PRIMARY KEY, figure VARCHAR(16) NOT NULL, source VARCHAR(10) NOT NULL,
  title VARCHAR(400), link VARCHAR(600), published TIMESTAMPTZ,
  matched_ticker VARCHAR(20) NOT NULL DEFAULT '', matched_name VARCHAR(150),
  in_universe BOOLEAN NOT NULL DEFAULT false, sentiment VARCHAR(8),
  date_verified BOOLEAN NOT NULL DEFAULT false, guid VARCHAR(320) NOT NULL,
  fetched_on TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_eb_mention UNIQUE (guid, matched_ticker));
"""
MERGE = """
INSERT INTO tbl_eb_mention_news (figure,source,title,link,published,matched_ticker,matched_name,in_universe,sentiment,guid)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
  ON CONFLICT (guid, matched_ticker) DO NOTHING
"""


def which_figure(title):
    """Return the figure key whose subject appears in the headline, or None."""
    for k, rx in _SUBJECT_RX.items():
        if rx.search(title or ""):
            return k
    return None


def verify_recent(conn, limit=20):
    """Same freshness discipline as trump_news: only provably-fresh items count.
    First-hand-ish (none here, all google) so we verify each candidate's real date."""
    cur = conn.cursor()
    dbex(cur, """SELECT id, title, link FROM tbl_eb_mention_news
                 WHERE source='google' AND date_verified=false AND in_universe=true
                   AND published >= now() - interval '10 days'
                 ORDER BY published DESC LIMIT %s""", limit)
    for r in cur.fetchall():
        real = article_date(r.link) or alt_source_date(r.title)
        if real and (dt.datetime.utcnow() - real).days <= 7:
            dbex(cur, "UPDATE tbl_eb_mention_news SET published=%s, date_verified=true WHERE id=%s",
                 real, r.id)
    conn.commit()


def main():
    print(f"== mention scan {dt.datetime.now():%Y-%m-%d %H:%M:%S} ==", flush=True)
    conn = get_conn(); cur = conn.cursor()
    cur.execute(DDL); conn.commit()
    tok_map, tick_map = build_matcher(cur)
    seen, ins = set(), 0

    for person in PEOPLE:
        for url in _feeds(person):
            try:
                f = feedparser.parse(url)
            except Exception as ex:
                print(f"  feed error {str(ex)[:50]}"); continue
            for e in f.entries:
                title = (getattr(e, "title", "") or "")[:400]
                fig = which_figure(title)
                if not fig:                          # headline must name a tracked figure/company
                    continue
                guid = (getattr(e, "id", None) or getattr(e, "link", "") or "")[:320]
                if not guid or guid in seen:
                    continue
                seen.add(guid)
                mtitle = strip_src(title)
                sent = sentiment(mtitle)
                if sent != "positive" or is_wrap(mtitle):
                    continue
                # 1) explicit ticker or cap-gated proximity name (Trump-grade, trusted)
                tk, nm, kind, tok = match_company(mtitle, tok_map, tick_map)
                ok = tk and (kind == "ticker" or (kind == "name" and pos_near(mtitle, tok)))
                # 2) the curated alias map catches clean 'figure praises Company' headlines
                if not ok:
                    atk, anm = alias_match(mtitle, fig)
                    if atk:
                        tk, nm, ok = atk, anm, True
                if ok:
                    dbex(cur, MERGE, fig, "google", title, (getattr(e, "link", "") or "")[:600],
                         pub(e), tk, nm, True, sent, guid)
                    ins += 1
            conn.commit()

    dbex(cur, """SELECT COUNT(*) n,
                 SUM(CASE WHEN in_universe AND sentiment='positive' THEN 1 ELSE 0 END) pos
                 FROM tbl_eb_mention_news""")
    row = cur.fetchone()
    print(f"mention_news: +{ins} this run | {row.n} total, {row.pos} positive & mapped")
    verify_recent(conn)
    conn.close()


if __name__ == "__main__":
    main()
