"""
validate.py - the engine's self-audit. Runs each day to keep the data clean and ON-BRIEF.
The principle: AUTO-FIX only what is unambiguous junk; FLAG anything that needs judgement.
Nothing here ever touches a HELD position or places a trade.

Auto-removed (clear junk, reversible-by-reseed):
  - watchlist / supply-link tickers that no longer resolve to a live price (delisted/dead)
  - supply links whose upstream is not in our universe and not flagged private
  - any NON-HELD watchlist name that now trips the mandate blacklist (biotech/Musk/crypto/cannabis)
  - duplicate watchlist rows (same sym)

Flagged for review (written to the audit log, NOT deleted):
  - watchlist names with no theme match in the pool AND no clear reason (possible off-brief drift)
  - keywords that have grown too broad (tag > BROAD_HITS companies) - vocabulary drift
  - watchlist size over the soft cap (too many candidates = noise)

A circuit breaker stops the run if it would change more than MAX_CHANGES things at once
(guards against a bug or bad upstream data cascading). Everything is logged to
tbl_eb_audit_log so the weekly brief can show "what the self-check did / flagged".
"""
import datetime as dt
import yfinance as yf
from eb_db import get_conn, dbex

MAX_CHANGES = 15          # circuit breaker: never auto-change more than this in one run
WATCHLIST_SOFT_CAP = 40   # over this, flag (don't delete) - too many candidates is noise
BROAD_HITS = 400          # a keyword tagging more than this is too broad -> flag

# Mandate blacklist - judged on the INDUSTRY/SECTOR classification (the structural fact),
# NOT loose words in the marketing summary. A company that merely SERVES pharma (e.g. a
# laser-cleaning machine maker) is not biotech; its industry would say 'Industrials'. This
# precision matters: a crude summary match falsely flagged LASE (industrial lasers) as pharma.
BLACK_INDUSTRY = ("biotechnology", "drug manufacturer", "pharmaceutical", "diagnostics & research",
                  "medical care", "health information", "gene", "therapeutic")
BLACK_NAME = ("therapeutics", "biosciences", "pharmaceuticals", "biopharma", "cannabis", "oncology")
CRYPTO = ("bitcoin", "crypto", "blockchain")        # only if it's the core business (industry/name)
MUSK = ("tesla", "spacex")


DDL = """
CREATE TABLE IF NOT EXISTS tbl_eb_audit_log (
  id SERIAL PRIMARY KEY, run_on TIMESTAMPTZ DEFAULT now(),
  action VARCHAR(12) NOT NULL,        -- 'removed' | 'flagged' | 'halt'
  target VARCHAR(40), kind VARCHAR(24), reason VARCHAR(300));
"""


def _live(sym):
    """True if the ticker still has a live price (not delisted)."""
    try:
        i = yf.Ticker(sym).info
        return bool(i.get("currentPrice") or i.get("regularMarketPrice"))
    except Exception:
        return None  # unknown - don't act on uncertainty


def _blacklisted(sym):
    """Blacklist on the structural classification, not summary words. Returns the reason
    string if blacklisted, else None. Conservative: only fires on industry/name, so a
    company that merely SERVES a blacklisted sector is not caught."""
    try:
        i = yf.Ticker(sym).info
    except Exception:
        return None
    industry = (i.get("industry") or "").lower()
    name = (i.get("longName") or i.get("shortName") or "").lower()
    if any(b in industry for b in BLACK_INDUSTRY):
        return f"industry is '{i.get('industry')}' (mandate blacklist)"
    if any(b in name for b in BLACK_NAME):
        return "company name indicates biotech/pharma/cannabis (mandate blacklist)"
    if any(m in industry or m in name for m in MUSK):
        return "Musk entity (mandate blacklist)"
    # crypto only if it's clearly the core business (industry or name), not a passing mention
    if any(cr in industry for cr in CRYPTO) or any(cr in name for cr in CRYPTO):
        return "crypto/blockchain core business (mandate blacklist)"
    return None


def main():
    conn = get_conn(); cur = conn.cursor()
    cur.execute(DDL); conn.commit()
    removals, flags = [], []

    def log(action, target, kind, reason):
        dbex(cur, "INSERT INTO tbl_eb_audit_log (action,target,kind,reason) VALUES (%s,%s,%s,%s)",
             action, target, kind, reason[:300])

    # ---- gather candidates (NEVER held) ----
    dbex(cur, "SELECT sym, name, held FROM tbl_eb_watchlist WHERE active=true AND held=false")
    watch = cur.fetchall()

    # 1. dead/delisted watchlist tickers
    for r in watch:
        if "." in r.sym:        # skip foreign listings (yfinance flaky on them) - don't false-remove
            continue
        live = _live(r.sym)
        if live is False:
            removals.append(("watchlist", r.sym, "delisted - no live price"))

    # 2. blacklist creep on non-held watchlist names
    for r in watch:
        reason = _blacklisted(r.sym)
        if reason:
            removals.append(("watchlist", r.sym, reason))

    # 3. duplicate watchlist rows
    dbex(cur, """SELECT sym FROM tbl_eb_watchlist WHERE active=true
                 GROUP BY sym HAVING COUNT(*) > 1""")
    for r in cur.fetchall():
        removals.append(("watchlist-dupe", r.sym, "duplicate active row"))

    # 4. supply links whose listed upstream is not in the universe (broken link)
    dbex(cur, """SELECT s.id, s.upstream FROM tbl_eb_supply_link s
                 LEFT JOIN tbl_eb_universe u ON u.yf_ticker = s.upstream
                 WHERE s.listed = true AND s.upstream NOT LIKE '%%.%%' AND u.yf_ticker IS NULL""")
    for r in cur.fetchall():
        removals.append(("supply-link", str(r.id), f"upstream {r.upstream} not in universe"))

    # ---- circuit breaker ----
    if len(removals) > MAX_CHANGES:
        log("halt", "-", "circuit-breaker",
            f"{len(removals)} removals proposed (> {MAX_CHANGES}) - HALTED, nothing changed. Review manually.")
        conn.commit()
        print(f"validate: HALTED - {len(removals)} changes exceeds breaker ({MAX_CHANGES}). Nothing removed.")
        conn.close()
        return

    # ---- apply removals (held is already excluded above) ----
    for kind, target, reason in removals:
        if kind == "watchlist":
            dbex(cur, "UPDATE tbl_eb_watchlist SET active=false WHERE sym=%s AND held=false", target)
        elif kind == "watchlist-dupe":
            dbex(cur, """DELETE FROM tbl_eb_watchlist WHERE sym=%s AND held=false
                         AND id NOT IN (SELECT MIN(id) FROM tbl_eb_watchlist WHERE sym=%s)""", target, target)
        elif kind == "supply-link":
            dbex(cur, "DELETE FROM tbl_eb_supply_link WHERE id=%s", int(target))
        log("removed", target, kind, reason)

    # ---- flags (review only, no deletion) ----
    # a) watchlist names with no pool theme match (possible off-brief drift)
    dbex(cur, """SELECT w.sym FROM tbl_eb_watchlist w
                 LEFT JOIN tbl_eb_pool p ON p.yf_ticker = w.sym
                 WHERE w.active=true AND w.held=false AND p.yf_ticker IS NULL""")
    for r in cur.fetchall():
        flags.append((r.sym, "watchlist", "no theme match in pool - confirm still on-brief"))

    # b) keywords grown too broad
    dbex(cur, "SELECT keyword FROM tbl_eb_sector_keywords WHERE active=true AND kind='include'")
    kws = [r.keyword for r in cur.fetchall()]
    for kw in kws:
        dbex(cur, "SELECT COUNT(*) n FROM tbl_eb_universe WHERE LOWER(name) LIKE %s", f"%{kw.lower()}%")
        n = cur.fetchone().n
        if n > BROAD_HITS:
            flags.append((kw, "keyword", f"too broad: matches {n} companies (> {BROAD_HITS})"))

    # c) watchlist over soft cap
    dbex(cur, "SELECT COUNT(*) n FROM tbl_eb_watchlist WHERE active=true AND held=false")
    n_watch = cur.fetchone().n
    if n_watch > WATCHLIST_SOFT_CAP:
        flags.append(("watchlist", "size", f"{n_watch} candidates (> {WATCHLIST_SOFT_CAP}) - prune the weakest"))

    for target, kind, reason in flags:
        log("flagged", target, kind, reason)

    conn.commit()
    print(f"validate: removed {len(removals)}, flagged {len(flags)}")
    for kind, target, reason in removals:
        print(f"  REMOVED {target} ({kind}): {reason}")
    for target, kind, reason in flags:
        print(f"  FLAG    {target} ({kind}): {reason}")
    conn.close()


if __name__ == "__main__":
    main()
