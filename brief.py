"""
brief.py - the weekly brief. The ONE email this project sends on a schedule.

Per SPEC.md (10/06/2026), the brief always has the same three parts:
  1. Up to three stocks worth considering - one fixed-format card each.
     If none earn a card, it says "nothing this week" and stops.
  2. A recommendation ("I recommend X because Y") - or a plain statement that
     nothing earns one.
  3. Anything we already hold or watch that needs attention.
Plus a short "stock pumps" section: companies a market-moving figure has backed (verified, <=7 days).

Writing rules are baked in here, not left to chance: plain British English, no
jargon (any unavoidable technical term is defined in brackets the first time),
one page, the same shape every time.

Two rulers:
  - proven supplier (profitable, established): judged on profits, growth,
    importance in the supply chain, and price against its own history.
  - pioneer (young, loss-making BY DESIGN - never rejected just for losing
    money): judged on whether the technology is real, who backs or buys from
    them, and how long their cash lasts.
"""
import datetime as dt
import html
from eb_db import get_conn, dbex
from stock_pumps import send_alert
from converge import cross_fund_convergence
from supply import chokepoints

P = "margin:0 0 8px;font-family:'Segoe UI',Arial,sans-serif;font-size:10pt;line-height:1.45;color:#1a1a1a"
H = "margin:18px 0 6px;font-family:'Segoe UI',Arial,sans-serif;font-size:10pt;font-weight:700;color:#1a1a1a"


def _money(v):
    if v is None:
        return "unknown"
    if v >= 1e9:
        return f"${v / 1e9:.1f} billion"
    return f"${v / 1e6:.0f} million"


def _linktag(sym):
    """A compact [link] anchor - the email never shows a raw URL."""
    return (f'<a href="https://finance.yahoo.com/quote/{sym}" '
            f'style="color:#1558d6;text-decoration:none">[link]</a>')


def _alink(url):
    """[link] anchor for a news article URL ('' if none)."""
    if not url:
        return ""
    return f' <a href="{html.escape(url, quote=True)}" style="color:#1558d6;text-decoration:none">[link]</a>'


def _first_sentence(text, fallback="No plain description available."):
    if not text:
        return fallback
    s = text.split(". ")[0].strip()
    if len(s) > 240:
        s = s[:237] + "..."
    return s if s.endswith(".") else s + "."


def _is_pioneer(r):
    """A pioneer is young/unprofitable; a proven supplier makes money at real scale."""
    profitable = (r.profit_margin or 0) > 0
    return not (profitable and (r.market_cap or 0) >= 2_000_000_000)


def _fetch_candidates(conn):
    cur = conn.cursor()
    dbex(cur, """
      WITH pool_g AS (
        SELECT yf_ticker, MIN(name) name, MIN(sector) sector, MIN(matched) matched
        FROM tbl_eb_pool WHERE fit='strong' GROUP BY yf_ticker),
      cat AS (
        SELECT yf_ticker, MAX(published) last_pub,
               (ARRAY_AGG(title ORDER BY published DESC))[1] last_title,
               (ARRAY_AGG(url ORDER BY published DESC))[1] last_url
        FROM tbl_eb_news WHERE catalyst=true AND published >= now() - interval '14 days'
        GROUP BY yf_ticker)
      SELECT g.yf_ticker, g.name, g.sector, g.matched,
             f.market_cap, f.price, f.range_pct, f.revenue_growth, f.profit_margin,
             f.total_cash, f.total_debt, f.summary,
             m.mv_1m, m.mv_3m, m.mv_6m,
             c.last_title, c.last_pub, c.last_url,
             w.sym IS NOT NULL AS on_watchlist
      FROM pool_g g
      JOIN tbl_eb_fundamentals f ON f.yf_ticker = g.yf_ticker
      LEFT JOIN tbl_eb_moves m ON m.yf_ticker = g.yf_ticker
      LEFT JOIN cat c ON c.yf_ticker = g.yf_ticker
      LEFT JOIN tbl_eb_watchlist w ON w.sym = g.yf_ticker AND w.active = true
      WHERE (f.price IS NULL OR f.price >= 0.10)""")
    return cur.fetchall()


def _score(r, conv):
    """Return (score, evidence). Evidence items are (sentence, article-url-or-None).
    Gate: needs at least one hard reason to appear."""
    ev, score, gated = [], 0, False
    funds = conv.get(r.yf_ticker)
    if r.last_pub is not None:
        score += 3
        gated = True
        ev.append((f"Fresh news ({r.last_pub:%d/%m}): {_first_sentence(r.last_title, r.last_title or '')}",
                   r.last_url))
    if funds and funds[0] >= 3:
        score += 3
        gated = True
        ev.append((f"Held by {funds[0]} of the big investment funds we track ({funds[1]}).", None))
    elif funds and funds[0] == 2:
        score += 1
        ev.append((f"Held by 2 of the big investment funds we track ({funds[1]}).", None))
    if (r.mv_1m or 0) <= -10 and (r.mv_6m or 0) > 0:
        score += 2
        gated = True
        ev.append((f"Down {abs(r.mv_1m):.0f}% this month while its 6-month trend is still up - "
                   "the dip-in-a-rising-trend entry our testing supports.", None))
    if (r.market_cap or 0) and r.market_cap < 1_500_000_000 and (r.mv_3m is None or r.mv_3m < 30):
        score += 1
        ev.append(("Still small and not yet moved - the early end of the field.", None))
    if (r.revenue_growth or 0) >= 0.25:
        score += 1
        ev.append((f"Sales growing fast ({r.revenue_growth * 100:.0f}% year on year).", None))
    if (r.mv_3m or 0) > 500:
        return 0, []          # already flown - chasing it is not being early
    return (score if gated else 0), ev


def _verdict(r, ev_count, funds, gated=True):
    """Plain rule-based verdict per the two rulers. Returns (verdict, reason).
    A BUY ('Consider buying small') needs a real TIMING trigger this week (gated = a fresh
    catalyst, fund-buying, or a dip-in-rising-trend) AND a trend that is not still falling.
    Without a timing reason a good name is 'Watch' - it may be a buy later, just not THIS week.
    A name still in a downtrend (1m and 3m both negative) is never a buy until the trend turns."""
    # A high (top of its range) is NOT a blocker - the strongest names live near their highs and
    # momentum is real. The real risk is a PARABOLIC run with no fundamental backing (a thin move
    # on hype). So: a buy needs growth + a real catalyst (gated) + an up trend. At-high is fine if
    # the run is backed; we just describe the entry (strength vs off-high) rather than gate on it.
    falling = (r.mv_1m or 0) < 0 and (r.mv_3m or 0) < 0    # still in a downtrend - wait for it to turn
    at_high = r.range_pct is not None and r.range_pct >= 85
    parabolic_unbacked = (r.mv_3m or 0) >= 60 and (r.revenue_growth or 0) < 0.15   # big move, weak growth
    if not _is_pioneer(r):
        growing = (r.revenue_growth or 0) >= 0.10
        entry = "buying into strength" if at_high else "off its high"
        if growing and ev_count >= 2 and gated and not falling and not parabolic_unbacked:
            return "Consider buying small", f"profitable, still growing, {entry}, with a reason to act now"
        if growing and falling:
            return "Watch", "good business, but the trend is still down - wait for it to turn before buying"
        if growing and parabolic_unbacked:
            return "Watch", "ran hard on thin fundamentals - let it prove the growth before buying"
        if growing:
            return "Watch", "good business, but nothing says buy it this week rather than later"
        return "Pass", "an established business that has stopped growing"
    # pioneer ruler - losing money is expected and is NOT a reason to pass
    cash_ok = (r.total_cash or 0) > (r.total_debt or 0)
    backed = bool(funds) or r.last_pub is not None
    if cash_ok and backed and ev_count >= 2 and gated and not falling and not parabolic_unbacked:
        return "Consider buying small", "early, has believers, cash to keep going, and a reason to act now"
    if cash_ok and falling:
        return "Watch", "interesting, but the trend is still down - wait for it to turn"
    if cash_ok:
        return "Watch", "interesting but no fresh reason to act this week"
    return "Pass", "more debt than cash - it would need to raise money on someone else's terms"


def _card(r, ev, conv, gated=True):
    funds = conv.get(r.yf_ticker)
    kind = "Pioneer" if _is_pioneer(r) else "Proven supplier"
    verdict, because = _verdict(r, len(ev), funds, gated)
    lines = [
        f"<b>{html.escape(r.name or r.yf_ticker)} ({r.yf_ticker}) - {verdict}</b> "
        f"{_linktag(r.yf_ticker)}<br>{html.escape(because[0].upper() + because[1:])}.",
        f"{kind}, company size {_money(r.market_cap)}.",
        f"<b>What they do:</b> {html.escape(_first_sentence(r.summary))}",
        f"<b>Where they sit:</b> {html.escape(r.sector or 'unclassified')} - flagged by our screen "
        f"for '{html.escape(r.matched or 'theme match')}'.",
    ]
    if _is_pioneer(r):
        cash, debt = r.total_cash, r.total_debt
        money_line = (f"It loses money, which is normal at this stage. Cash {_money(cash)} against "
                      f"debt {_money(debt)} - "
                      + ("a healthy buffer." if (cash or 0) > (debt or 0) else "a real concern."))
        lines.append(f"<b>The test (pioneer):</b> {money_line}")
    else:
        lines.append(
            f"<b>The test (proven supplier):</b> profit margin "
            f"{(r.profit_margin or 0) * 100:.0f}% (of every $1 of sales, "
            f"{(r.profit_margin or 0) * 100:.0f} cents is profit); sales growth "
            f"{(r.revenue_growth or 0) * 100:.0f}% year on year; trading at "
            f"{r.range_pct or 0:.0f}% of its one-year price range (0 = at its low, 100 = at its high).")
    for text, url in ev:
        lines.append("&bull; " + html.escape(text) + _alink(url))
    return f"<p style=\"{P}\">" + "<br>".join(lines) + "</p>", verdict, because


def _comparison(rows_by_sector, r):
    peers = [x.yf_ticker for x in rows_by_sector.get(r.sector, []) if x.yf_ticker != r.yf_ticker][:4]
    if not peers:
        return None
    return f"Others on our radar in the same area: {', '.join(peers)}."


def section_candidates(conn, conv):
    rows = _fetch_candidates(conn)
    scored = []
    for r in rows:
        if r.on_watchlist:
            continue            # watchlist names live in part 3, not part 1
        s, ev = _score(r, conv)
        if s > 0:
            scored.append((s, r, ev))
    scored.sort(key=lambda x: -x[0])
    by_sector = {}
    for _, r, _e in scored[:25]:
        by_sector.setdefault(r.sector, []).append(r)
    blocks, rec = [], None
    for s, r, ev in scored:
        if len(blocks) >= 3:
            break
        verdict, because = _verdict(r, len(ev), conv.get(r.yf_ticker))
        if verdict == "Pass":
            continue          # a name we would pass on is not "worth considering"
        card_html, verdict, because = _card(r, ev, conv)
        comp = _comparison(by_sector, r)
        if comp:
            card_html = card_html.replace("</p>", f"<br><i>{html.escape(comp)}</i></p>")
        blocks.append(card_html)
        if rec is None and verdict == "Consider buying small":
            reasons = " ".join(t for t, _u in ev[:2]) if ev else because
            rec = (f"{r.name or r.yf_ticker} ({r.yf_ticker}). Why: {reasons} "
                   f"{'A pioneer - keep any stake small.' if _is_pioneer(r) else 'A proven supplier.'}")
    return blocks, rec


def section_watchlist(conn, conv):
    cur = conn.cursor()
    dbex(cur, """
      SELECT w.sym, w.name, w.sector, w.priority, w.held, w.noted, w.noted_price, w.why, w.triggers,
             f.price, m.mv_1m, m.mv_6m,
             c.last_title, c.last_pub, c.last_url
      FROM tbl_eb_watchlist w
      LEFT JOIN tbl_eb_fundamentals f ON f.yf_ticker = w.sym
      LEFT JOIN tbl_eb_moves m ON m.yf_ticker = w.sym
      LEFT JOIN (SELECT yf_ticker, MAX(published) last_pub,
                        (ARRAY_AGG(title ORDER BY published DESC))[1] last_title,
                        (ARRAY_AGG(url ORDER BY published DESC))[1] last_url
                 FROM tbl_eb_news WHERE catalyst=true AND published >= now() - interval '7 days'
                 GROUP BY yf_ticker) c ON c.yf_ticker = w.sym
      WHERE w.active = true
      ORDER BY w.held DESC, CASE WHEN w.priority='high' THEN 0 ELSE 1 END, w.sym""")
    actions, updates = [], []
    for r in cur.fetchall():
        signals = []          # (sentence, article-url-or-None)
        if (r.mv_1m or 0) <= -10 and (r.mv_6m or 0) > 0:
            signals.append(("it is in the dip-in-a-rising-trend buying window our testing supports", None))
        if r.last_pub is not None:
            signals.append((f"fresh news ({r.last_pub:%d/%m}): "
                            f"{_first_sentence(r.last_title, r.last_title or '')}", r.last_url))
        if r.noted_price and r.price:
            chg = (r.price - r.noted_price) / r.noted_price * 100
            if abs(chg) >= 25:
                signals.append((f"it has moved {chg:+.0f}% since we first noted it", None))
        funds = conv.get(r.sym)
        if funds and funds[0] >= 3:
            signals.append((f"{funds[0]} of the big funds we track now hold it", None))
        if not signals:
            continue
        # ACTION: every Section 3 name gets a clear action, not just a description.
        # Buy needs a timing reason (a buying-window dip or a fresh catalyst); a downtrend with no
        # such reason is Hold; a held name whose trend has broken hard is flagged to review.
        in_window = (r.mv_1m or 0) <= -10 and (r.mv_6m or 0) > 0
        fresh_cat = r.last_pub is not None
        broken = (r.mv_1m or 0) <= -25 and (r.mv_6m or 0) < 0     # hard drop AND trend rolled over
        if r.held:
            if in_window or fresh_cat:
                action, areason = "BUY MORE", ("in the dip-in-a-rising-trend window" if in_window else "on the fresh catalyst")
            elif broken:
                action, areason = "REVIEW - trend broken", "down hard and the longer trend has rolled over - check the thesis still holds"
            else:
                action, areason = "HOLD", "no action needed this week"
        else:
            if in_window or fresh_cat:
                action, areason = "BUY", ("in the dip-in-a-rising-trend window" if in_window else "on the fresh catalyst")
            else:
                action, areason = "WATCH", "no reason to act this week"
        tag = " (held - real money)" if r.held else (" (high priority)" if r.priority == "high" else "")
        body = (f"<b>{html.escape(r.name or r.sym)} ({r.sym}) - {action}</b>{tag} {_linktag(r.sym)}<br>"
                f"<i>{html.escape(areason)}.</i> "
                + "; ".join(html.escape(t) + _alink(u) for t, u in signals) + ".")
        # what's changed in its position - always shown for context
        pos = []
        if r.price:
            pos.append(f"now ${r.price:,.2f}")
        if r.price and r.noted_price:
            chg = (r.price - r.noted_price) / r.noted_price * 100
            since = f" since we noted it on {r.noted:%d/%m/%Y}" if r.noted else " since we noted it"
            pos.append(f"{chg:+.0f}%{since}")
        if r.mv_1m is not None:
            pos.append(f"{r.mv_1m:+.0f}% over the past month")
        if pos:
            body += f"<br><i>Position: {html.escape(', '.join(pos))}.</i>"
        if r.triggers and any("buying window" in t for t, _u in signals):
            body += f"<br>Our trigger notes for it: {html.escape(_first_sentence(r.triggers))}"
        item = f"<p style=\"{P}\">{body}</p>"
        # 3.1 Actions = needs a decision this week (buy / buy more / sell-review).
        # 3.2 Watchlist updates = everything else (hold, notable move, news worth knowing).
        if action in ("BUY", "BUY MORE", "SELL", "REVIEW - trend broken"):
            actions.append(item)
        else:
            updates.append(item)
    return actions, updates


def section_inbound(conn):
    """Names Claude Trades flagged on price action this week + what EarlyBird's auto-research
    concluded. Cross-engine: a name on-brief in EarlyBird AND strong-trend in Claude Trades is the
    sharpest signal; a CT name EarlyBird finds off-brief is flagged as a trade-portfolio idea, not
    an EarlyBird one. All are 'confirm' - the deep catalyst pass happens when you greenlight."""
    cur = conn.cursor()
    try:
        dbex(cur, """SELECT yf_ticker, growth_score, portfolio, status, eb_verdict
                     FROM tbl_eb_inbound
                     WHERE researched_on >= now() - interval '8 days' AND status <> 'dup'
                     ORDER BY (status='researched') DESC, growth_score DESC NULLS LAST LIMIT 12""")
        rows = cur.fetchall()
    except Exception as ex:
        print(f"inbound section unavailable: {ex}")
        return []
    if not rows:
        return []
    out = []
    for r in rows:
        tag = "on-brief - auto-watchlisted" if r.status == "researched" else "off-brief - trade idea only"
        sc = f"growth_score {r.growth_score:.1f}" if r.growth_score is not None else ""
        pf = f", Portfolio {r.portfolio}" if r.portfolio else ""
        out.append(f"<p style=\"{P}\"><b>{html.escape(r.yf_ticker)}</b> "
                   f"({html.escape(sc)}{html.escape(pf)}) - {html.escape(tag)}. "
                   f"<i>{html.escape((r.eb_verdict or '')[:160])}</i> {_linktag(r.yf_ticker)}</p>")
    return out


# which figure pumped it -> plain label for the brief
_FIG_LABEL = {"trump": "Trump", "huang": "Nvidia/Huang", "nadella": "Microsoft/Nadella",
              "pichai": "Google/Pichai", "jassy": "Amazon/Jassy", "zuck": "Meta/Zuckerberg",
              "altman": "OpenAI/Altman", "su": "AMD/Su",
              "bezos": "Bezos/Blue Origin", "wood": "Cathie Wood/ARK",
              "son": "SoftBank/Son", "intel": "Intel/Tan"}


def section_pumps(conn):
    """Stock pumps - established names a market-moving figure (Trump, Huang, the hyperscaler
    CEOs, Altman, Su) has backed in the last 7 days, dates verified. One line per ticker."""
    cur = conn.cursor()
    try:
        dbex(cur, """
          SELECT DISTINCT ON (matched_ticker) matched_ticker, matched_name, figure,
                 LEFT(title, 90) t, published, link
          FROM tbl_eb_pump_news
          WHERE in_universe = true AND sentiment = 'positive' AND date_verified = true
            AND published >= now() - interval '7 days'
          ORDER BY matched_ticker, published DESC""")
        rows = cur.fetchall()
    except Exception as ex:
        print(f"pumps unavailable: {ex}"); return []
    return [f"<p style=\"{P}\"><b>{html.escape(r.matched_name or r.matched_ticker)}</b> "
            f"({r.published:%d/%m}, via {_FIG_LABEL.get(r.figure, r.figure or 'a figure')}): "
            f"{html.escape(r.t or '')}{_alink(r.link)} {_linktag(r.matched_ticker)}</p>"
            for r in rows[:8]]


def section_chokepoints(conn):
    """The structurally-protected names: critical input, few/sole suppliers, no substitute, AND
    sold out / backlogged right now. A supplier nobody can route around has pricing power and a
    real moat - worth knowing which of these are buyable. Only listed names, only the hot ones."""
    try:
        rows = chokepoints(conn, hot_only=True)
    except Exception as ex:
        print(f"chokepoints unavailable: {ex}"); return []
    # only buyable names (have a ticker), de-dup by ticker already handled in chokepoints()
    rows = [r for r in rows if r.upstream and "(pvt" not in (r.upstream_name or "").lower()]
    return [f"<p style=\"{P}\"><b>{html.escape(r.upstream_name or r.upstream)}</b> "
            f"[{html.escape(r.criticality or '')}/{html.escape(r.exclusivity or '')}]: "
            f"{html.escape(r.constraint_note or 'supply constrained')} {_linktag(r.upstream)}</p>"
            for r in rows[:8]]


def section_radar(conn):
    """Emerging themes the engine has no keyword for yet - clusters of terms appearing across
    several moving names. The engine proposes; you decide whether any becomes a real theme.
    Only multi-word (specific) candidates, and ones that have shown up across multiple runs
    OR score high - a one-day blip is filtered, a building wave is surfaced."""
    cur = conn.cursor()
    try:
        dbex(cur, """SELECT phrase, n_tickers, tickers, example, runs
                     FROM tbl_eb_theme_candidate
                     WHERE status = 'new' AND brief_worthy = true
                       AND last_seen >= now() - interval '10 days'
                     ORDER BY (runs >= 2) DESC, score DESC
                     LIMIT 5""")
        rows = cur.fetchall()
    except Exception as ex:
        print(f"radar unavailable: {ex}"); return []
    out = []
    for r in rows:
        recur = " (recurring)" if (r.runs or 1) >= 2 else ""
        out.append(f"<p style=\"{P}\"><b>&ldquo;{html.escape(r.phrase)}&rdquo;</b>{recur} "
                   f"- seen across {r.n_tickers} moving names ({html.escape(r.tickers or '')}). "
                   f"<i>{html.escape((r.example or '')[:110])}</i></p>")
    return out


def section_selfcheck(conn):
    """What the daily self-validate did this week - removals (junk pruned) and flags
    (things needing your eye). Keeps the autonomous engine honest and visible."""
    cur = conn.cursor()
    try:
        dbex(cur, """SELECT action, target, kind, reason FROM tbl_eb_audit_log
                     WHERE run_on >= now() - interval '7 days'
                       AND action IN ('removed','flagged','halt')
                     ORDER BY CASE action WHEN 'halt' THEN 0 WHEN 'removed' THEN 1 ELSE 2 END, run_on DESC
                     LIMIT 12""")
        rows = cur.fetchall()
    except Exception as ex:
        print(f"selfcheck unavailable: {ex}"); return []
    out = []
    for r in rows:
        verb = {"removed": "Removed", "flagged": "Flag", "halt": "HALTED"}.get(r.action, r.action)
        out.append(f"<p style=\"{P}\"><b>{verb}: {html.escape(r.target or '')}</b> "
                   f"({html.escape(r.kind or '')}) - {html.escape(r.reason or '')}</p>")
    return out


def build_and_send(conn):
    try:
        conv_rows, _period = cross_fund_convergence(conn, min_funds=2, limit=60)
        conv = {r["ticker"]: (r["funds"], r["funds_list"]) for r in conv_rows if r["ticker"]}
    except Exception as ex:
        print(f"convergence unavailable: {ex}")
        conv = {}

    cards, rec = section_candidates(conn, conv)
    wl_actions, wl_updates = section_watchlist(conn, conv)
    pumps = section_pumps(conn)
    choke = section_chokepoints(conn)
    radar = section_radar(conn)
    inbound = section_inbound(conn)
    selfcheck = section_selfcheck(conn)

    today = dt.date.today().strftime("%d/%m/%Y")
    parts = [f"<p style=\"{P}\">EarlyBird weekly brief, {today}. Three parts, same every week: "
             "the recommendation, ideas worth considering, and anything we hold "
             "or watch that needs attention.</p>"]

    parts.append(f"<p style=\"{H}\">1. Recommendation</p>")
    parts.append(f"<p style=\"{P}\">{html.escape(rec) if rec else 'Nothing earns a recommendation this week.'}</p>")

    parts.append(f"<p style=\"{H}\">2. Worth considering this week</p>")
    if cards:
        parts.append(f"<p style=\"{P}\"><i>Verdicts: 'Consider buying small' = worth a position "
                     "now, kept small. 'Watch' = interesting, but wait for a clear trigger. "
                     "Names that fail their test are left out entirely.</i></p>")
        parts.extend(cards)
    else:
        parts.append(f"<p style=\"{P}\">Nothing this week. No stock produced strong enough "
                     "evidence to earn a card, so none is shown. That is the system working, "
                     "not failing.</p>")

    parts.append(f"<p style=\"{H}\">3. Holdings and watchlist</p>")

    parts.append(f"<p style=\"{H}\">3.1 Actions</p>")
    if wl_actions:
        parts.append(f"<p style=\"{P}\"><i>Names that need a decision this week - buy, add, or "
                     "review for selling. Each says why.</i></p>")
        parts.extend(wl_actions)
    else:
        parts.append(f"<p style=\"{P}\">Nothing to act on this week.</p>")

    parts.append(f"<p style=\"{H}\">3.2 Watchlist updates</p>")
    if wl_updates:
        parts.append(f"<p style=\"{P}\"><i>Names we hold or watch with notable news or moves, but "
                     "no action needed - for your information.</i></p>")
        parts.extend(wl_updates)
    else:
        parts.append(f"<p style=\"{P}\">No other updates this week.</p>")

    if pumps:
        parts.append(f"<p style=\"{H}\">Stock pumps - companies a big name has backed (7 days, dates checked)</p>")
        parts.extend(pumps)

    if choke:
        parts.append(f"<p style=\"{H}\">Chokepoints - critical suppliers that are sold out right now</p>")
        parts.append(f"<p style=\"{P}\"><i>These make something everyone needs, have few or no "
                     "rivals, can't be substituted, and can't keep up with demand. A real moat - "
                     "not a buy signal on its own, but names worth knowing.</i></p>")
        parts.extend(choke)

    if radar:
        parts.append(f"<p style=\"{H}\">Theme radar - new ideas the engine spotted forming</p>")
        parts.append(f"<p style=\"{P}\"><i>Terms cropping up across several moving names that we "
                     "don't yet track as a theme. Possible next waves. None is acted on - they're "
                     "here for you to say whether any is worth following.</i></p>")
        parts.extend(radar)

    if inbound:
        parts.append(f"<p style=\"{H}\">From Claude Trades - price-action picks, cross-checked</p>")
        parts.append(f"<p style=\"{P}\"><i>Names the Claude Trades engine surfaced on price action "
                     "(strong growth trend), auto-checked against EarlyBird. 'On-brief' = both engines "
                     "agree, auto-watchlisted - confirm it. 'Off-brief' = a good trend but not future-tech, "
                     "a trade-portfolio idea not an EarlyBird one.</i></p>")
        parts.extend(inbound)

    if selfcheck:
        parts.append(f"<p style=\"{H}\">Self-check - what the engine cleaned or flagged this week</p>")
        parts.extend(selfcheck)

    parts.append(f"<p style=\"{P}\"><i>Reminder: pioneers lose money by design and are small "
                 "stakes only. A recommendation is a prompt to look, not an instruction.</i></p>")

    body = "<div style='max-width:680px'>" + "".join(parts) + "</div>"
    ok = send_alert(f"EarlyBird weekly brief - {today}", body)
    print(f"brief: {len(cards)} cards, rec={'yes' if rec else 'no'}, "
          f"{len(watch)} watchlist items, {len(pumps)} stock-pump items, "
          f"{len(selfcheck)} self-check items, emailed={ok}")
    return ok


if __name__ == "__main__":
    conn = get_conn()
    build_and_send(conn)
    conn.close()
