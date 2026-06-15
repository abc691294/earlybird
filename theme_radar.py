"""
theme_radar.py - discover EMERGING themes the engine has no keyword for yet.

The gap this fills: the screen can only find what its keywords already name. Nothing
actively looks for the NEXT 'physical AI' before someone coins the term. This does.

How it works (no model, just counting - cheap, runs daily):
  1. Pull recent news headlines (tbl_eb_news) + sector RSS (tbl_eb_sector_news), last N days.
  2. Weight headlines towards names that are MOVING (tbl_eb_moves.mv_3m) but are NOT already
     a strong-fit pool name - i.e. the market cares but our themes don't catch them yet.
  3. Extract candidate phrases (1-3 word terms) from those headlines.
  4. Drop anything we ALREADY cover: existing sector keywords, common finance/stopword noise.
  5. Count each phrase by how many DISTINCT tickers (or sector feeds) carry it - breadth, not
     repetition, is what makes a theme. A term on 1 noisy ticker is noise; on 8 names it's a wave.
  6. Surface the top clusters as CANDIDATE themes, with example names and headlines.

The engine PROPOSES; you decide. Nothing is auto-promoted to a real theme - a candidate
becomes a theme only when you say so (the autonomy guardrail: buy/no-buy and what-counts-as-a
-theme are yours). Results land in tbl_eb_theme_candidate and the weekly brief surfaces the top few.

  python theme_radar.py                    # scan, print candidates, write to tbl_eb_theme_candidate
  python theme_radar.py --days 14          # widen the window
  python theme_radar.py --dry              # scan and print only, don't write
  python theme_radar.py --promote "spacex ipo"   # you decide: turn into a real theme (add keywords)
  python theme_radar.py --dismiss "long term"    # you decide: it's noise, stop showing it
"""
import sys
import re
import datetime as dt
from collections import defaultdict
from eb_db import get_conn, dbex

DAYS = 21          # look-back window for headlines
MIN_TICKERS = 4    # a phrase must appear across this many distinct names to count as a wave
MAX_TICKERS = 40   # a phrase on MORE names than this is generic market-speak, not a theme
TOP = 20           # candidates to surface

# Noise we never want as a "theme". Finance boilerplate, units, and generic market words -
# these clutter headlines without naming a sector. Kept deliberately tight; the keyword filter
# below removes terms we already cover, so this only needs to catch generic cruft.
_STOP = set("""
the a an and or of to in on for with at by from as is are was be been has have had will would
this that these those it its their his her our your my you we they he she i us them him
new now today week month year quarter q1 q2 q3 q4 fy update report says said say according
stock stocks share shares price prices target buy sell hold rating upgrade downgrade analyst
analysts earnings revenue profit loss guidance dividend market markets trading trade investors
investor billion million trillion percent rose fell jump jumps surge surges drop drops gain gains
high low close open after before amid vs than more most less best worst top down up over under
company companies inc corp ltd plc group holdings nyse nasdaq etf fund index dow sp500 nasdaq100
ceo cfo deal deals announce announces announced launch launches plan plans report reports
how why what when who which could should may might can will just get got make made set sets
growth valuation value valued undervalued overvalued know need here there strong weak recent
highlights street wall momentum rally story assessing assess data tech technology june july
may march april february january august september october november december reasons reason
attention attracting investment investors worth estimated fair below above premium discount
pick picks watch radar trade traded beat beats results result guidance subscription led
ownership insider buying selling shares outstanding cap small large mid micro global
narrative execution risk risks risky hopes shift shifting cooling heating gain gained
opportunity opportunities undervalued top bottom check checks signals signal mixed fundamental
fundamentals position positions positioned outlook forecast estimate estimates consensus
late consider continue rebound since outperforming outperform better option options right
record highs high low lead leads leading bonanza upside downside move moves moving rise rises
rising fall falls falling climb climbs drives drive pre bell premarket futures equities equity
depositary receipts adr asian european us stock stocks vs versus solutions spotlight partnership
hits week 52 today about other another year stunned shares left
""".split())

# Single tech words we ALREADY know are themes - if a phrase is only these, it's not 'emerging'.
# (Multi-word combos containing them, e.g. 'humanoid robot', can still surface.)
_KNOWN_SINGLES = set("""ai chip chips semiconductor quantum nuclear uranium drone drones defense
defence space satellite cyber cybersecurity battery lithium robot robotics solar grid power""".split())

_WORD = re.compile(r"[a-z][a-z0-9\-]{2,}")


def _phrases(text):
    """1-, 2- and 3-word lowercase phrases from a headline.

    Build n-grams over the ORIGINAL token order (don't pre-strip stopwords - that glues
    unrelated words across gaps, e.g. '...too late to consider...' -> 'too late consider').
    A phrase is only kept if it neither starts nor ends with a stopword, and contains no
    stopword in the middle either (a real theme term is a clean noun phrase)."""
    toks = _WORD.findall((text or "").lower())
    out = set()
    for i in range(len(toks)):
        for span in (1, 2, 3):
            if i + span > len(toks):
                break
            gram = toks[i:i + span]
            if any(w in _STOP for w in gram):     # any stopword anywhere -> not a clean phrase
                continue
            out.add(" ".join(gram))
    return out


def _known_keywords(conn):
    """Every active sector keyword, lowercased - phrases matching one of these are already covered."""
    cur = conn.cursor()
    dbex(cur, "SELECT DISTINCT lower(keyword) k FROM tbl_eb_sector_keywords WHERE active")
    return {r.k for r in cur.fetchall()}


def _is_covered(phrase, known):
    """True if we already track this - exact keyword match, or a single word we know is a theme."""
    if phrase in known:
        return True
    if " " not in phrase and phrase in _KNOWN_SINGLES:
        return True
    # a phrase that merely CONTAINS a known keyword as a whole word is covered too
    for k in known:
        if k and (phrase == k or f" {k} " in f" {phrase} " or phrase.startswith(k + " ") or phrase.endswith(" " + k)):
            return True
    return False


def scan(conn, days=DAYS):
    """Return candidate phrases sorted by breadth (distinct movers carrying them)."""
    known = _known_keywords(conn)
    cur = conn.cursor()

    # movers we DON'T already strongly catch: market cares (mv_3m), theme doesn't (no strong fit).
    # These are the headlines most likely to carry a term we lack.
    dbex(cur, """
        SELECT n.yf_ticker, n.title, m.mv_3m,
               COALESCE((SELECT max(CASE WHEN p.fit='strong' THEN 2 WHEN p.fit IS NOT NULL THEN 1 ELSE 0 END)
                         FROM tbl_eb_pool p WHERE p.yf_ticker = n.yf_ticker), 0) fit_lvl
        FROM tbl_eb_news n
        JOIN tbl_eb_moves m ON m.yf_ticker = n.yf_ticker
        WHERE n.published >= now() - make_interval(days => %s)
          AND m.mv_3m IS NOT NULL""", days)
    news = cur.fetchall()

    # phrase -> set of distinct tickers, plus example headlines and a weight
    tickers = defaultdict(set)
    examples = defaultdict(list)
    weight = defaultdict(float)
    for r in news:
        # weight a headline up if the name is moving hard AND we don't strongly catch it.
        # a strong-fit name's headlines are about a theme we already have - down-weight them.
        mv = abs(r.mv_3m or 0)
        w = (1.0 if mv > 0.15 else 0.4) * (1.0 if r.fit_lvl == 0 else 0.5 if r.fit_lvl == 1 else 0.15)
        for ph in _phrases(r.title):
            if _is_covered(ph, known):
                continue
            tickers[ph].add(r.yf_ticker)
            weight[ph] += w
            if len(examples[ph]) < 3 and r.title not in examples[ph]:
                examples[ph].append(r.title)

    # also fold in sector-RSS terminology (no ticker, but counts as feed breadth)
    dbex(cur, """SELECT sector, COALESCE(title,'') || ' ' || COALESCE(summary,'') txt
                 FROM tbl_eb_sector_news WHERE published >= now() - make_interval(days => %s)""", days)
    feeds = defaultdict(set)
    for r in cur.fetchall():
        for ph in _phrases(r.txt):
            if _is_covered(ph, known):
                continue
            feeds[ph].add(r.sector)

    # score = distinct movers (breadth) + weighted lift, with a small bonus for cross-feed mentions.
    cands = []
    for ph, tks in tickers.items():
        n = len(tks)
        words = ph.split()
        # BREADTH WINDOW: too few names = noise on one ticker; too many = generic market-speak
        # ('growth' is on 280 names and means nothing). A real emerging theme sits in between.
        if n < MIN_TICKERS or n > MAX_TICKERS:
            continue
        # a theme is a SPECIFIC term. Bare single words are almost always generic - require a
        # multi-word phrase, OR a single word that is long and distinctive (e.g. 'humanoid').
        if len(words) == 1 and len(ph) < 8:
            continue
        if len(ph) < 4:
            continue
        # multi-word phrases are far more likely to name a real theme - reward specificity.
        specificity = 1.0 + 0.8 * (len(words) - 1)
        score = (n + weight[ph] + 0.5 * len(feeds.get(ph, ()))) * specificity
        cands.append({
            "phrase": ph, "n_tickers": len(tks), "score": round(score, 1),
            "tickers": sorted(tks)[:8], "feeds": sorted(feeds.get(ph, ())),
            "examples": examples[ph],
            # multi-word phrases are brief-worthy; single words are too ambiguous to show the
            # user as a 'theme' but stay in the table for the trend (runs++) signal.
            "brief_worthy": len(words) >= 2,
        })
    cands.sort(key=lambda c: c["score"], reverse=True)
    # de-overlap: drop a shorter phrase fully contained in a higher-ranked longer one (and vice
    # versa) so 'humanoid' and 'humanoid robot' don't both fill slots - keep the more specific.
    kept, seen_words = [], []
    for c in cands:
        words = set(c["phrase"].split())
        if any(words < w or w < words for w in seen_words):
            continue
        kept.append(c)
        seen_words.append(words)
        if len(kept) >= TOP:
            break
    return kept


DDL = """
CREATE TABLE IF NOT EXISTS tbl_eb_theme_candidate (
  phrase TEXT PRIMARY KEY,
  n_tickers INTEGER, score DOUBLE PRECISION,
  tickers TEXT, example TEXT,
  first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
  runs INTEGER NOT NULL DEFAULT 1,
  status VARCHAR(10) NOT NULL DEFAULT 'new',   -- new / promoted / dismissed
  reviewed BOOLEAN NOT NULL DEFAULT false,
  brief_worthy BOOLEAN NOT NULL DEFAULT true   -- multi-word phrase, specific enough to show
);
ALTER TABLE tbl_eb_theme_candidate ADD COLUMN IF NOT EXISTS brief_worthy BOOLEAN NOT NULL DEFAULT true;
"""


def persist(conn, cands):
    """Upsert candidates. A phrase that recurs across runs gets runs++ and a bumped last_seen -
    persistence over time is itself signal (a one-day blip fades; a real theme keeps showing up).
    Never touches a row you've already promoted or dismissed."""
    cur = conn.cursor()
    cur.execute(DDL)
    now = dt.datetime.now(dt.timezone.utc)
    for c in cands:
        dbex(cur, """
            INSERT INTO tbl_eb_theme_candidate (phrase, n_tickers, score, tickers, example, last_seen, brief_worthy)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (phrase) DO UPDATE SET
              n_tickers = EXCLUDED.n_tickers, score = EXCLUDED.score,
              tickers = EXCLUDED.tickers, example = EXCLUDED.example,
              last_seen = EXCLUDED.last_seen, brief_worthy = EXCLUDED.brief_worthy,
              runs = tbl_eb_theme_candidate.runs + 1
            WHERE tbl_eb_theme_candidate.status = 'new'""",
            c["phrase"], c["n_tickers"], c["score"],
            ", ".join(c["tickers"]), (c["examples"][0] if c["examples"] else "")[:300], now,
            c["brief_worthy"])
    conn.commit()


def review(conn, phrase, status):
    """Mark a candidate promoted (you'll add keywords) or dismissed (noise). Stops it recurring
    in the brief and freezes its row so the daily scan won't bump it again."""
    cur = conn.cursor()
    dbex(cur, "UPDATE tbl_eb_theme_candidate SET status=%s, reviewed=true WHERE phrase=%s", status, phrase)
    conn.commit()
    print(f"{phrase!r} -> {status}")


def main():
    args = sys.argv[1:]
    # review shortcuts: theme_radar.py --promote "spacex ipo"  /  --dismiss "long term"
    if args and args[0] in ("--promote", "--dismiss"):
        status = "promoted" if args[0] == "--promote" else "dismissed"
        review(get_conn(), " ".join(args[1:]).strip().lower(), status)
        return
    days = DAYS
    dry = "--dry" in args
    if "--days" in args:
        days = int(args[args.index("--days") + 1])
    conn = get_conn()
    cands = scan(conn, days)
    print(f"\nEMERGING THEME RADAR - {len(cands)} candidate clusters (last {days} days)")
    print("(terms we have no keyword for, appearing across multiple moving names)\n")
    for c in cands:
        feeds = f"  feeds: {', '.join(c['feeds'])}" if c["feeds"] else ""
        print(f"  {c['score']:5}  \"{c['phrase']}\"  ({c['n_tickers']} names: {', '.join(c['tickers'])}){feeds}")
        if c["examples"]:
            ex = c["examples"][0][:100].encode("ascii", "replace").decode()
            print(f"           e.g. {ex}")
    if not dry:
        persist(conn, cands)
        print(f"\nWrote {len(cands)} candidates to tbl_eb_theme_candidate (status='new' until you review).")
    conn.close()


if __name__ == "__main__":
    main()
