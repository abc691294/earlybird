"""supply_discover.py - auto-discover supplier -> customer links from the news feed using the
Claude API, and PROPOSE them for the supply-chain map (you confirm; the engine never auto-trusts).

The supply map (tbl_eb_supply_link) was hand-seeded - accurate but only ~12% complete. This reads
the relationship-language headlines already in tbl_eb_news ("X supplies Y", "X wins order from Y",
"X to fab for Y", "partners with", "selected by"...) and uses a CHEAP model (Haiku) to extract the
structured fact behind each: who is the supplier, who is the customer, what is supplied. It is the
exact thing I do by hand when you give me a ticker - reading the news and judging where it sits in a
chain - pointed at the whole feed instead of one name at a time.

PROPOSE-YOU-DECIDE (same guardrail as the theme radar): discovered links are written with
source='discover-candidate', NOT trusted. They show up in the brief for you to confirm. Confirming
is a one-liner (UPDATE ... SET source='auto'); supply.py reads ALL links regardless of source, so a
confirmed-vs-candidate distinction is purely for review - nothing is hidden, nothing is auto-acted.

Guardrails baked in:
  - Only proposes links whose BOTH ends resolve to a real ticker in tbl_eb_universe (we never
    invent a name we cannot buy/track; unresolved ends are dropped and logged).
  - UNIQUE(theme,downstream,upstream,role) + ON CONFLICT DO NOTHING means a candidate that already
    exists as a hand-seeded link is silently skipped - no duplicates, no overwrite of confirmed facts.
  - Mechanical only: it proposes the LINK (who feeds whom + role). It does NOT set criticality /
    chokepoint fields - those stay a hand judgement on confirm.

Model: claude-haiku-4-5 - entity extraction from a one-line headline is Haiku's job; cheap and fast,
~240 catalyst headlines/month is trivial volume. (No Opus - per the standing rule.)

SELF-THROTTLES TO WEEKLY + INCREMENTAL: it tracks the last news id it processed and the last run
date in tbl_eb_discover_state. On a normal (cron) run it does nothing unless (a) 7+ days have passed
since the last run AND (b) new relationship headlines have arrived since the last processed id. So it
can sit in the DAILY job harmlessly - it only spends a model call about once a week, and only on
genuinely-new headlines. --force ignores the weekly gate; --days falls back to a time window (and
also ignores the gate) for a manual sweep.

Usage:  python supply_discover.py            # weekly self-gated, only NEW headlines since last run
        python supply_discover.py --force    # run now regardless of the 7-day gate (still incremental)
        python supply_discover.py --days 90  # manual sweep of a time window (ignores the gate + cursor)
        python supply_discover.py --dry-run  # extract + print, write nothing, advance no cursor
"""
import argparse
import json
import os
import re

import anthropic

from eb_db import get_conn, dbex

MODEL = "claude-haiku-4-5"

# Headlines that PLAUSIBLY describe a supplier->customer relationship. A cheap pre-filter so we
# only spend a model call on headlines that could carry a link (not every catalyst is a relationship).
REL_HINTS = re.compile(
    r"\b(supplies?|supplier|supply agreement|to supply|fab(?:s|ricat)|foundry|wafers?|"
    r"order from|order for|wins? order|awarded|selected by|selected for|qualifies?|design win|"
    r"partners? with|partnership|collaborat|to provide|provides?|chips? for|components? for|"
    r"to manufacture|manufactures? for|sole supplier|secures? (?:deal|contract|order))\b",
    re.I,
)

SYSTEM = (
    "You read a single business-news headline and extract the supplier->customer relationship it "
    "states, if any. You are building a tech supply-chain map (semiconductors, AI hardware, defence, "
    "space, quantum, nuclear/power, cybersecurity). Return ONLY a JSON object - no prose.\n\n"
    "Schema:\n"
    '  {"link": true/false,\n'
    '   "supplier_ticker": "<US/UK exchange ticker of the company that SUPPLIES/makes/provides>",\n'
    '   "supplier_name": "<company name>",\n'
    '   "customer_ticker": "<ticker of the company that RECEIVES/buys/is fed>",\n'
    '   "customer_name": "<company name>",\n'
    '   "role": "<what is supplied, <=6 words: e.g. HBM memory, EUV lithography, foundry, etch tools>",\n'
    '   "theme": "<one of: AI, Semis, Defence, Space, Quantum, Nuclear, Power, Cyber>"}\n\n'
    "Rules:\n"
    '- link=false if the headline is NOT a clear supplier->customer fact (analyst opinion, a price '
    "move, a generic partnership with no clear direction, M&A, earnings).\n"
    "- Give the STOCK TICKER you are confident of (AVGO, NVDA, TSM, ASML, MU...). If you do not know "
    "the ticker for an end, put null for that ticker but still give the name.\n"
    "- supplier = the picks-and-shovels (makes the input); customer = the one whose product needs it.\n"
    "- Do not guess a relationship the headline does not state. When unsure, link=false."
)


def _extract(client, title):
    """One Haiku call -> the structured link (or {'link': False}). Returns a dict; {} on any error."""
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=SYSTEM,
            messages=[{"role": "user", "content": f"Headline: {title}"}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "").strip()
        # tolerate code-fenced or prefixed JSON
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}
    except Exception as ex:
        print(f"  extract failed: {str(ex)[:80]}")
        return {}


def _resolve(cur, ticker, name):
    """Return a real universe ticker for this end, or None. We never propose a link to a name we
    cannot track. STRICT to avoid misresolutions (a loose substring name-match wrongly mapped
    'Theon'->'Pantheon' and a CNTX biotech->Centrus): trust the model's VALIDATED ticker first;
    fall back to name ONLY on an exact full-name match (not a substring). A near-miss returns None
    (-> the link is dropped as unresolved), which is the safe failure - better a missed link than
    a wrong one. The candidate-review step is the backstop, not the first line of defence."""
    if ticker:
        t = ticker.strip().upper()
        dbex(cur, "SELECT yf_ticker FROM tbl_eb_universe WHERE upper(yf_ticker)=%s LIMIT 1", t)
        r = cur.fetchone()
        if r:
            return r.yf_ticker
    if name:
        n = name.strip()
        # EXACT full-name match only. No substring ('Theon' must not hit 'Pantheon') and no prefix
        # ('Micron' must not pick 'Micron Solutions' over 'Micron Technology'). If the model sends a
        # bare/ambiguous name, this returns None and the link is dropped as unresolved - the safe
        # failure. The model reliably sends tickers for names that matter, so this rarely bites.
        dbex(cur, "SELECT yf_ticker FROM tbl_eb_universe WHERE lower(name)=lower(%s) LIMIT 1", n)
        r = cur.fetchone()
        if r:
            return r.yf_ticker
    return None


def _on_brief(cur, ticker):
    """True if this ticker is in the strong pool (EarlyBird already judges it on-brief future-tech).
    Used to AUTO-CONFIRM high-confidence links and only surface borderline ones for your eye."""
    dbex(cur, "SELECT 1 FROM tbl_eb_pool WHERE yf_ticker=%s AND fit='strong' LIMIT 1", ticker)
    return cur.fetchone() is not None


STATE_DDL = """CREATE TABLE IF NOT EXISTS tbl_eb_discover_state (
  id            INT PRIMARY KEY DEFAULT 1,
  last_news_id  INT DEFAULT 0,
  last_run_on   TIMESTAMPTZ
)"""
WEEKLY_GAP_DAYS = 7


def _state(cur):
    """Return (last_news_id, last_run_on) - the cursor + when we last ran. Seeds the row on first use."""
    dbex(cur, STATE_DDL)
    dbex(cur, "INSERT INTO tbl_eb_discover_state (id, last_news_id) VALUES (1, 0) ON CONFLICT (id) DO NOTHING")
    dbex(cur, "SELECT last_news_id, last_run_on FROM tbl_eb_discover_state WHERE id=1")
    r = cur.fetchone()
    return (r.last_news_id or 0), r.last_run_on


def discover(conn, days=None, dry_run=False, force=False):
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env / .env (loaded by eb_db import)
    cur = conn.cursor()
    last_id, last_run = _state(cur)

    if days is not None:
        # manual time-window sweep: ignore the weekly gate AND the incremental cursor.
        dbex(cur, """SELECT id, title FROM tbl_eb_news
                     WHERE published >= now() - (%s || ' days')::interval AND title IS NOT NULL
                     ORDER BY id""", str(days))
        scope = f"last {days}d (window sweep - cursor not moved)"
    else:
        # weekly gate: skip silently unless 7+ days since last run (--force overrides).
        if not force and last_run is not None:
            dbex(cur, "SELECT (now() - %s) >= make_interval(days => %s) due", last_run, WEEKLY_GAP_DAYS)
            if not cur.fetchone().due:
                print(f"supply_discover: last run {last_run:%Y-%m-%d}, < {WEEKLY_GAP_DAYS}d ago - skipping (weekly).")
                return 0
        # incremental: only headlines newer than the last id we processed.
        dbex(cur, """SELECT id, title FROM tbl_eb_news
                     WHERE id > %s AND title IS NOT NULL ORDER BY id""", last_id)
        scope = f"new since news id {last_id}"

    rows = cur.fetchall()
    max_seen = max([r.id for r in rows], default=last_id)
    titles = [r.title for r in rows if r.title and REL_HINTS.search(r.title)]
    print(f"supply_discover: {len(titles)} relationship-language headlines ({scope})")

    proposed, dropped, dup = 0, 0, 0
    for title in titles:
        d = _extract(client, title)
        if not d or not d.get("link"):
            continue
        up = _resolve(cur, d.get("supplier_ticker"), d.get("supplier_name"))
        down = _resolve(cur, d.get("customer_ticker"), d.get("customer_name"))
        if not up or not down or up == down:
            dropped += 1
            print(f"  drop (unresolved): {d.get('supplier_name')} -> {d.get('customer_name')}  [{title[:60]}]")
            continue
        theme = (d.get("theme") or "AI")[:40]
        role = (d.get("role") or "")[:80]
        # AUTO-CONFIRM the high-confidence links (both ends already strong-pooled = EarlyBird
        # already judges both on-brief future-tech) so the map grows hands-off. Only links with a
        # borderline end (off-brief or unpooled) stay 'discover-candidate' and surface in the brief
        # for your one-line decision. This mirrors inbound.py: auto-act on the clear ones, flag the rest.
        both_pooled = _on_brief(cur, up) and _on_brief(cur, down)
        src = "auto" if both_pooled else "discover-candidate"
        note = ("auto-confirmed (both ends on-brief) from news: " if both_pooled
                else "candidate from news: ") + title[:220]
        print(f"  {'CONFIRM' if both_pooled else 'PROPOSE'} [{theme}] {up} -> feeds {down}  ({role})")
        if dry_run:
            proposed += 1
            continue
        # layer 1 (direct supplier per the headline). Conflict target is the LIVE dedupe constraint
        # uq_supply_link (upstream, downstream) - one link per supplier->customer pair regardless of
        # role wording - so a pair already mapped (hand-seeded or proposed earlier this run) is
        # silently skipped, never duplicated and never overwriting a confirmed link.
        dbex(cur, """INSERT INTO tbl_eb_supply_link
              (theme, downstream, upstream, upstream_name, layer, role, listed, note, source, added_on)
              VALUES (%s, %s, %s, %s, 1, %s, true, %s, %s, now())
              ON CONFLICT (upstream, downstream) DO NOTHING""",
              theme, down, up, d.get("supplier_name"), role, note, src)
        if cur.rowcount:
            proposed += 1
        else:
            dup += 1

    if not dry_run:
        # Advance the cursor + stamp the run ONLY on an incremental run (not a --days window sweep,
        # which would otherwise skip headlines the weekly run hasn't processed). The window sweep
        # still writes its proposed links - it just leaves the cursor where the weekly run left it.
        if days is None:
            dbex(cur, "UPDATE tbl_eb_discover_state SET last_news_id=%s, last_run_on=now() WHERE id=1", max_seen)
        conn.commit()
    print(f"supply_discover: proposed {proposed} candidate link(s), "
          f"{dup} already-known, {dropped} unresolved-and-dropped."
          + ("  (dry-run: nothing written)" if dry_run else ""))
    return proposed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None,
                    help="manual time-window sweep (ignores the weekly gate + incremental cursor)")
    ap.add_argument("--force", action="store_true", help="run now, ignore the 7-day weekly gate")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = get_conn()
    discover(conn, days=args.days, dry_run=args.dry_run, force=args.force)
    conn.close()


if __name__ == "__main__":
    main()
