"""
stock_pumps.py - daily scan for ESTABLISHED stocks that market-moving FIGURES have spoken
POSITIVELY about ("stock pumps"). Trump is ONE such figure; the others are Jensen Huang /
Nvidia, the hyperscaler CEOs (Nadella, Pichai, Jassy, Zuckerberg), Sam Altman and Lisa Su.

When one of these figures praises a company, takes a stake, or strikes a partnership, the
named stock tends to move that day - often before it reaches the wider feed. We catch it.

Intent (per spec):
  - Capture ANY company a tracked figure mentions (not limited to our universe), but...
  - ...focus on ESTABLISHED players (large caps), which is what the Wave sweep targets, and
  - ...keep only POSITIVE sentiment (praise / backing / a favourable move), not attacks.

Sources:
  A) DISCOVERY - Google News RSS per figure. Mapped to a company by explicit ticker, by a
     cap-gated established-company NAME, or (for the figures) by a curated alias map.
  B) TRUMP-SPECIFIC PRIMARY - White House actions + Truth Social (read at source = early),
     plus the policy->beneficiary layer (a tariff/trade action that moves a whole sector).

Sentiment is classified from the headline; only 'positive' rows surface in the brief, and
only if the true publish date is verified and <= 7 days old. Stored in tbl_eb_pump_news
(with a `figure` column saying which pumper), deduped. Run daily.
"""
import os, re, json, ssl, smtplib, html, datetime as dt
import urllib.request, urllib.parse
from pathlib import Path
from email.mime.text import MIMEText
import feedparser
from eb_db import get_conn

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
_DATE_RX = [
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.I),
    re.compile(r'property=["\']article:published_time["\']\s+content=["\']([^"\']+)', re.I),
    re.compile(r'content=["\']([^"\']+)["\']\s+property=["\']article:published_time["\']', re.I),
    re.compile(r'name=["\'](?:pubdate|publishdate|publish-date|date)["\']\s+content=["\']([^"\']+)', re.I),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)', re.I),
]


def _http(url, timeout=8, data=None, ctype=None, limit=400_000):
    h = {"User-Agent": _UA, "Accept-Language": "en-GB,en;q=0.9", "Referer": "https://news.google.com/"}
    if ctype:
        h["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(limit).decode("utf-8", "ignore")


def resolve_gnews(url, timeout=8):
    """Decode a Google News RSS link to the real publisher URL via Google's batchexecute
    endpoint. Returns the publisher URL or None. Google News links don't HTTP-redirect."""
    if "news.google.com" not in (url or ""):
        return url
    try:
        page = _http(url, timeout, limit=2_000_000)
        sg = re.search(r'data-n-a-sg="([^"]+)', page)
        ts = re.search(r'data-n-a-ts="([^"]+)', page)
        aid = re.search(r'data-n-a-id="([^"]+)', page)
        if not (sg and ts and aid):
            return None
        inner = json.dumps(["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en",
                None, 1, None, None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1,
                None, 0, 0, None, 0], aid.group(1), int(ts.group(1)), sg.group(1)])
        freq = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        body = urllib.parse.urlencode({"f.req": freq}).encode()
        resp = _http("https://news.google.com/_/DotsSplashUi/data/batchexecute", timeout,
                     body, "application/x-www-form-urlencoded;charset=UTF-8")
        m = re.search(r'(https?://(?!news\.google\.com)[^\\"]+)', resp.replace("\\/", "/"))
        return m.group(1).rstrip("\\") if m else None
    except Exception:
        return None


def article_date(url, timeout=8):
    """Resolve a story's REAL publish date: decode the Google News link to the publisher,
    then read the publisher's date metadata. Returns a naive UTC datetime, or None if it
    can't be determined (publisher blocks bots). Google re-dates recycled articles, so the
    RSS date alone is not trustworthy for freshness."""
    real = resolve_gnews(url, timeout)
    if not real:
        return None
    try:
        raw = _http(real, timeout)
    except Exception:
        return None
    for rx in _DATE_RX:
        m = rx.search(raw)
        if not m:
            continue
        s = m.group(1).strip()
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            return (d - d.utcoffset()).replace(tzinfo=None) if d.tzinfo else d
        except Exception:
            mm = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
            if mm:
                return dt.datetime(int(mm[1]), int(mm[2]), int(mm[3]))
    return None


def alt_source_date(title, timeout=8, max_tries=3):
    """Freshness fallback (spec 10/06/2026): when a story's own page won't give up its
    date, look for an ALTERNATIVE source covering the same story and verify that one.
    Searches Google News for the headline's distinctive words (last 7 days only) and
    returns the first verifiable publish date found, or None."""
    base = strip_src(title)
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'&.-]+", base) if w.lower() not in _STOP][:6]
    if len(words) < 3:
        return None
    q = urllib.parse.quote(" ".join(words) + " when:7d")
    try:
        f = feedparser.parse(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")
    except Exception:
        return None
    for e in f.entries[:max_tries]:
        d = article_date(getattr(e, "link", "") or "", timeout)
        if d is not None:
            return d
    return None


def dbex(cur, sql, *params):
    """psycopg execute that accepts pyodbc-style positional params (wrapped to a tuple)."""
    cur.execute(sql, params if params else None)

SECRETS = Path(__file__).parent / "secrets.json"  # same gitignored file weekly_report.py uses


def wave_text(label, ticker, interval):
    """One Wave line for a timeframe, e.g.
    'Weekly Wave: Neutral, last signal - Strong Buy (29/05/2026)'.
    Replicates the 'Wave RSI v0.1' Pine indicator (WaveTrend / VuManChu Cipher B):
    channel 9, average 12, MA 3; ob 60, os -40, sell ob_extreme 75, gold (Strong Buy) <= -80.
    Returns None if data unavailable."""
    try:
        import yfinance as yf
        period = "3y" if interval == "1wk" else "2y"
        h = yf.Ticker(ticker).history(period=period, interval=interval)
        if len(h) < 60:
            return None
        src = (h["High"] + h["Low"] + h["Close"]) / 3
        esa = src.ewm(span=9, adjust=False).mean()
        de = (src - esa).abs().ewm(span=9, adjust=False).mean()
        ci = (src - esa) / (0.015 * de)
        wt1 = ci.ewm(span=12, adjust=False).mean()
        wt2 = wt1.rolling(3).mean()
        w1 = float(wt1.iloc[-1])
        cu = (wt1.shift(1) <= wt2.shift(1)) & (wt1 > wt2)   # crossover up
        cd = (wt1.shift(1) >= wt2.shift(1)) & (wt1 < wt2)   # crossunder down
        sig, sdate = "n/a", None
        for i in range(len(wt1) - 1, max(0, len(wt1) - 300), -1):  # most recent CROSS, either way
            if cu.iloc[i]:                                          # crossed up
                sig = "Strong Buy" if wt2.iloc[i] <= -80 else ("Buy" if wt2.iloc[i] <= -40 else "turning up")
                sdate = wt1.index[i]
                break
            if cd.iloc[i]:                                          # crossed down (cancels any older buy)
                sig = "Sell" if wt2.iloc[i] >= 75 else "rolling over"
                sdate = wt1.index[i]
                break
        zone = "Overbought" if w1 >= 60 else "Oversold" if w1 <= -40 else "Neutral"
        datepart = f" ({sdate.strftime('%d/%m/%Y')})" if sdate is not None else ""
        return f"{label}: {zone}, last signal - {sig}{datepart}"
    except Exception:
        return None
# the high-conviction alert trigger. Originally just position-TAKING (Trump bought a stake),
# but the scanner now also covers ENDORSEMENTS (a figure saying 'buy X'), so the list must catch
# both. Missing 'buy' meant 'Trump says BUY Nokia' - the strongest possible signal - never fired
# (only a weak 'investment' headline did). Endorsement verbs added.
# Long stems are safe as substrings; short ambiguous ones ('buy' must not hit 'buyback'/'buyer')
# match as a whole word via a boundary regex.
_BUYTIER = ("bought", "stake", "disclos", "invest", "acquir", "endorse", "prais", "tout")
_BUYTIER_WORD = ("buy", "buys", "backs", "backed")     # whole-word only


def _buytier_sql(col="title"):
    """SQL boolean: a buy-tier endorsement/position term in `col`. Substring for long stems,
    whole-word (Postgres \\m..\\M) for short ambiguous ones so 'buy' != 'buyback'."""
    subs = [f"{col} ILIKE '%{k}%'" for k in _BUYTIER]
    words = [f"{col} ~* '\\m{k}\\M'" for k in _BUYTIER_WORD]
    return "(" + " OR ".join(subs + words) + ")"


def send_alert(subject, body):
    """Email via the existing Gmail SMTP path. Returns True on success (so callers only
    mark rows 'alerted' when delivery actually happened). Silent no-op if secrets missing."""
    try:
        gu = os.environ.get("GMAIL_USER")
        if gu:
            user, pw = gu, os.environ["GMAIL_APP_PASSWORD"]
            recip = os.environ.get("GMAIL_RECIPIENT", gu)
        else:
            cfg = json.loads(SECRETS.read_text())
            user, pw = cfg["gmail_user"], cfg["gmail_app_password"]
            recip = cfg.get("recipient", user)
        msg = MIMEText(body, "html")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = recip
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as srv:
            srv.login(user, pw)
            srv.send_message(msg)
        return True
    except Exception as ex:
        print(f"  alert email skipped: {str(ex)[:70]}")
        return False

GNEWS = [
 'https://news.google.com/rss/search?q=Trump%20(stock%20OR%20shares%20OR%20stocks)%20when:4d&hl=en-US&gl=US&ceid=US:en',
 'https://news.google.com/rss/search?q=Trump%20(praises%20OR%20touts%20OR%20backs%20OR%20great)%20company%20when:5d&hl=en-US&gl=US&ceid=US:en',
 'https://news.google.com/rss/search?q=%22Trump%20said%22%20(shares%20OR%20stock)%20when:5d&hl=en-US&gl=US&ceid=US:en',
 # premium signal: Trump's actual disclosed positions (the Dell/Boeing/Nvidia pattern)
 'https://news.google.com/rss/search?q=Trump%20(bought%20OR%20stake%20OR%20%22disclosed%22%20OR%20invests)%20(stock%20OR%20shares)%20when:6d&hl=en-US&gl=US&ceid=US:en',
]

EST_CAP = 50_000_000_000  # name-based matching only for MEGA caps >= $50B (famous,
# distinctive names - Boeing/Dell/Apple/IBM/Caterpillar). Below this, name tokens
# (clean/china/brady) collide with unrelated headlines, so require an explicit ticker.

# ============================================================================
# THE PUMPERS - market-moving figures beyond Trump. Each: a regex that must appear in the
# headline (the figure or their company), and the people who drive the Google feeds.
# ============================================================================
FIGURES = {
    "trump":   r"\bTrump\b",
    "huang":   r"\b(Jensen Huang|Nvidia|NVIDIA)\b",
    "nadella": r"\b(Satya Nadella|Nadella)\b",
    "pichai":  r"\b(Sundar Pichai|Pichai)\b",
    "jassy":   r"\b(Andy Jassy|Jassy)\b",
    "zuck":    r"\b(Mark Zuckerberg|Zuckerberg)\b",
    "altman":  r"\b(Sam Altman|Altman)\b",
    "su":      r"\b(Lisa Su)\b",
    # really key players whose positive word re-rates a stock
    "bezos":   r"\b(Jeff Bezos|Bezos|Blue Origin)\b",      # space / Blue Origin signal, + Amazon
    "wood":    r"\b(Cathie Wood|ARK Invest|ARK Innovation)\b",  # moves small-cap future-tech
    "son":     r"\b(Masayoshi Son|SoftBank)\b",            # AI/chip names (Arm, AI bets)
    "intel":   r"\b(Intel CEO|Lip-Bu Tan)\b",              # chip supply-chain / foundry roadmap
    # surfaced by the theme radar as recurring market-mover figures (no own ticker - they
    # comment on OTHER names): Cramer moves stocks on a CNBC mention; Aschenbrenner's AI calls
    # (Situational Awareness, now runs an AI-focused fund) re-rate AI/compute names.
    "cramer":  r"\b(Jim Cramer|Cramer|Mad Money)\b",
    "aschenbrenner": r"\b(Leopold Aschenbrenner|Aschenbrenner|Situational Awareness)\b",
    "patel":   r"\b(Dylan Patel|SemiAnalysis)\b",          # semis/AI-compute calls re-rate chip names
}
_FIGURE_RX = {k: re.compile(v) for k, v in FIGURES.items()}
# the people whose names drive the discovery feeds (Trump has his own GNEWS + primary sources)
PEOPLE = ["Jensen Huang", "Satya Nadella", "Sundar Pichai", "Andy Jassy",
          "Mark Zuckerberg", "Sam Altman", "Lisa Su",
          "Jeff Bezos", "Cathie Wood", "Masayoshi Son", "Lip-Bu Tan",
          "Jim Cramer", "Leopold Aschenbrenner", "Dylan Patel"]
# each figure's own company - we want who they BACK, not a headline that's merely about them.
# (Bezos/Amazon kept as own so an Amazon-results story doesn't tag AMZN; his SPACE comments
# still map to the launch names via the alias map + 'Blue Origin' in his regex.)
OWN_TICKER = {"huang": "NVDA", "nadella": "MSFT", "pichai": "GOOGL", "jassy": "AMZN",
              "zuck": "META", "altman": None, "su": "AMD", "trump": None,
              "bezos": "AMZN", "wood": None, "son": None, "intel": "INTC",
              "cramer": None, "aschenbrenner": None, "patel": None}


def which_figure(title):
    for k, rx in _FIGURE_RX.items():
        if rx.search(title or ""):
            return k
    return None


def figure_feeds(person):
    """Google News RSS for one pumper - praise + partnership/investment patterns."""
    q = person.replace(" ", "%20")
    return [
        f"https://news.google.com/rss/search?q={q}%20(praises%20OR%20touts%20OR%20backs%20OR%20partners%20OR%20partnership)%20when:5d&hl=en-US&gl=US&ceid=US:en",
        f"https://news.google.com/rss/search?q={q}%20(invests%20OR%20stake%20OR%20deal%20OR%20supply%20OR%20%22to%20buy%22)%20(stock%20OR%20shares%20OR%20company)%20when:6d&hl=en-US&gl=US&ceid=US:en",
    ]

# Curated aliases for the big tech names the figures actually discuss. The cap-gated token
# map drops common words (meta/amazon/apple); here the headlines are clean ("X praises Meta")
# so a tight explicit map is safe. An endorsement verb must sit within 40 chars of the name.
_ALIASES = {
    r"\bMeta\b": ("META", "Meta Platforms"), r"\bMicrosoft\b": ("MSFT", "Microsoft"),
    r"\bAmazon\b": ("AMZN", "Amazon"), r"\b(?:Google|Alphabet)\b": ("GOOGL", "Alphabet"),
    r"\bApple\b": ("AAPL", "Apple"), r"\bBroadcom\b": ("AVGO", "Broadcom"),
    r"\bMarvell\b": ("MRVL", "Marvell"), r"\bMicron\b": ("MU", "Micron"),
    r"\bCorning\b": ("GLW", "Corning"), r"\bCoreWeave\b": ("CRWV", "CoreWeave"),
    r"\bOracle\b": ("ORCL", "Oracle"), r"\bPalantir\b": ("PLTR", "Palantir"),
    r"\bTSMC\b": ("TSM", "TSMC"), r"\bIntel\b": ("INTC", "Intel"),
    r"\bDell\b": ("DELL", "Dell"), r"\bSuper\s?Micro\b": ("SMCI", "Super Micro"),
}
_ALIAS_RX = [(re.compile(p, re.I), tk, nm) for p, (tk, nm) in _ALIASES.items()]
_ENDORSE = re.compile(
    r"\b(prais\w*|tout\w*|hail\w*|back(?:s|ed|ing)|endors\w*|partner\w*|invest\w*|stake|"
    r"deal|supply|buys?|bought|acquir\w*|loves?|picks?|chose|selects?)\b", re.I)


def alias_match(title, figure_key):
    """Map a clean 'figure backs Company' headline to a ticker, with an endorsement verb near
    the company name (kills wealth-ranking / comparison noise), skipping the figure's own co."""
    own = OWN_TICKER.get(figure_key)
    low = title or ""
    for rx, tk, nm in _ALIAS_RX:
        if tk == own:
            continue
        m = rx.search(low)
        if m and _ENDORSE.search(low[max(0, m.start() - 40): m.end() + 40]):
            return tk, nm
    return None, None

DDL = """
IF OBJECT_ID('tbl_eb_pump_news','U') IS NULL
CREATE TABLE tbl_eb_pump_news (
  id INT IDENTITY PRIMARY KEY, source VARCHAR(10) NOT NULL,
  title NVARCHAR(400) NULL, link NVARCHAR(600) NULL, published DATETIME2 NULL,
  matched_ticker NVARCHAR(20) NULL, matched_name NVARCHAR(150) NULL, in_universe BIT NOT NULL DEFAULT 0,
  sentiment VARCHAR(8) NULL, guid VARCHAR(320) NOT NULL,
  fetched_on DATETIME2 NOT NULL DEFAULT now(),
  CONSTRAINT UQ_eb_trump UNIQUE (guid, matched_ticker));
"""
MERGE = """
INSERT INTO tbl_eb_pump_news (source,title,link,published,matched_ticker,matched_name,in_universe,sentiment,guid)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
  ON CONFLICT (guid, matched_ticker) DO NOTHING
"""
# Trump rows leave `figure` at its 'trump' default; the figure scan sets it explicitly.
FIGURE_MERGE = """
INSERT INTO tbl_eb_pump_news (figure,source,title,link,published,matched_ticker,matched_name,in_universe,sentiment,guid)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
  ON CONFLICT (guid, matched_ticker) DO NOTHING
"""

_STOP = {"trump","stock","stocks","shares","company","companies","group","global","market","markets",
         "solutions","services","products","brands","enterprises","incorporated","limited",
         "first","united","american","america","national","corp","holdings","technologies",
         "technology","systems","industries","international","capital","financial","energy",
         "media","news","power","motors","electric","digital","health","world","value",
         "growth","future","money","trust","partners","general","standard",
         "china","chinese","korea","japan","japanese","israel","europe","european",
         "australia","australian","canada","canadian","britain","british","mexico","mexican",
         "india","indian","france","french","germany","german","spain","spanish","brazil",
         "brady","clean","green","smart","quantum","nuclear","global","value",
         "visa","data","auto","gold","semi","cyber","solar","wind","cloud",
         "great","honor","honour","patriot","respect","respected","fantastic","tiger",
         "congratulations","candidate","endorse","border","strong","american"}
_SUFFIX = re.compile(r"\b(inc|ltd|limited|corp|corporation|plc|holdings?|group|technolog\w*|"
                     r"co|sa|ag|nv|se|the|company|systems?|industries|international)\b")

# sentiment - price/Trump-favourable language wins even alongside 'tariff' (e.g. domestic
# steel rallying on tariffs is positive for those names); pure-negative is attack language.
_POS = re.compile(r"\b(prais\w*|tout\w*|hail\w*|back(?:s|ed|ing)|endors\w*|boost\w*|rall\w*|surg\w*|"
                  r"soar\w*|jump\w*|rebound\w*|gain\w*|higher|loves?|favou?r\w*|"
                  r"trump effect|wins?|win\b|deal|optimism|pop\b|"
                  r"disclos\w*|buys?|bought|purchas\w*|invest\w*|stake|acquir\w*)\b", re.I)
_NEG = re.compile(r"\b(slam\w*|blast\w*|attack\w*|criticis\w*|criticiz\w*|threat\w*|prob\w*|"
                  r"lawsuit|sues?|sued|feud|penal\w*|fraud|plung\w*|sink\w*|tumbl\w*|"
                  r"plummet\w*|crash\w*|warn\w*|ban\b|bans\b|"
                  # 'weighs heavy/heavily on' - unambiguous single-company drag ('SoftBank stake
                  # weighs heavy as shares shed 16%'). Deliberately NOT adding shed/slump/slide:
                  # those have bullish counter-uses (buy-back, 'bought the slump') + appear in
                  # oil/market-wrap noise, so a blanket rule causes false negatives.
                  r"weighs? heav(?:y|ily))\b", re.I)
# don't-buy / wait language - a figure can say "I like it BUT don't buy yet". That's a hold,
# not a pump, even though 'buy'/'like'/'deal' would otherwise read positive. Overrides _POS.
_HOLD = re.compile(r"(do(?:n'?t| not) (?:want you to )?buy|wouldn'?t buy|not (?:a )?buy\b|"
                   r"avoid buying|hold off|table buying|wait (?:to|before|until) buy|"
                   r"too early to buy|don'?t (?:chase|touch))", re.I)
# NOT-A-PUMP: the company is the DONOR/spender on a political/charitable/social programme, not a
# stock a figure backed. "Micron Announces $250M Investment to Support Trump Accounts for Children"
# reads positive via 'investment'+'Trump' but is corporate giving, not an endorsement of the stock.
_NOTPUMP = re.compile(r"(trump account|for children|child(?:ren)? and famil|donat\w*|charit\w*|"
                      r"philanthrop\w*|foundation|to support|in support of|pledge\w*|gives? back|"
                      r"scholarship|community (?:fund|program)|relief fund|disaster relief)", re.I)
_WRAP = re.compile(r"\b(dow|s&p|nasdaq|futures|wall street|stock market today|markets? today)\b", re.I)
_TRUMP = re.compile(r"\bTrump\b")  # proper noun only - excludes verb "trumps"


def is_trump(t): return bool(_TRUMP.search(t or ""))
def is_wrap(t):  return bool(_WRAP.search(t or ""))


def strip_src(title):
    """Google News headlines end with ' - Publisher' (e.g. ' - Investing.com Australia').
    That suffix is NOT article content - matching against it caused false hits (a country
    word in the publisher matched a company name; 'Investing.com' faked positive sentiment).
    Drop the final ' - <publisher>' segment for matching; the full title is still stored."""
    t = title or ""
    return t.rsplit(" - ", 1)[0].strip() if " - " in t else t


def sentiment(title):
    t = title or ""
    if _HOLD.search(t):  # explicit "don't buy / wait" overrides any positive verb -> not a pump
        return "neutral"
    if _NOTPUMP.search(t):  # corporate donation/charity/programme, not a stock endorsement -> not a pump
        return "neutral"
    # CRASH/negative language OVERRIDES positive. A plunging stock is NOT a pump even if the
    # headline also says 'stake'/'deal'/'invest' (e.g. "DJT plunge erases $766M from Trump's
    # STAKE" - 'plunge' wins, not 'stake'). NEG was being beaten by POS-checked-first - the bug
    # that tagged JPM/QS/DJT crash stories positive. Check NEG before POS.
    if _NEG.search(t):
        return "negative"
    if _POS.search(t):   # price/Trump-favourable language present -> positive
        return "positive"
    return "neutral"


def _name_token(name):
    if not name:
        return None
    w = _SUFFIX.sub(" ", name.lower())
    # min len 4 (catches Dell/Ford); the $50B cap gate keeps short common-word tokens
    # (macy) out. A few 4-letter common words that ARE mega-caps are stoplisted (visa).
    words = [x for x in re.findall(r"[a-z]+", w) if len(x) >= 4 and x not in _STOP]
    return words[0] if words else None


def build_matcher(cur):
    """ticker map (all) + established-name token map (cap-gated, largest cap per token).
    Honours THE single exclusion list via fn_eb_excluded (tobacco, crypto, cannabis, biotech,
    Musk, ...), so a figure mentioning e.g. BTI never gets logged as a pump. One list, shared
    with the screen and the validator - no blacklist drift."""
    dbex(cur, """SELECT u.yf_ticker, u.name, COALESCE(f.market_cap,0) cap,
                        (EXISTS (SELECT 1 FROM tbl_eb_pool p WHERE p.yf_ticker=u.yf_ticker)
                         OR EXISTS (SELECT 1 FROM tbl_eb_watchlist w WHERE w.sym=u.yf_ticker AND w.active)) on_brief
                   FROM tbl_eb_universe u
                   LEFT JOIN tbl_eb_fundamentals f ON f.yf_ticker=u.yf_ticker
                   WHERE u.active=true AND NOT fn_eb_excluded(u.yf_ticker)""")
    tick_tmp, tok_best = {}, {}
    for r in cur.fetchall():
        yf = r.yf_ticker or ""
        base = yf.split(".")[0].upper()
        if 2 <= len(base) <= 6:
            is_us = "." not in yf                        # US primary listings have no suffix
            ex = tick_tmp.get(base)
            if ex is None or (is_us and not ex[2]):      # prefer the US listing on a base collision
                tick_tmp[base] = (yf, r.name, is_us)     # (TKO -> TKO Group US, not TKO.PA Tikehau)
        # name-token match ONLY for on-brief names (pool or watchlist). A $50B off-mandate name
        # (TJX retail) must not become matchable by a generic word in its name, or it gets falsely
        # tied to unrelated Trump/oil headlines.
        if (r.cap or 0) >= EST_CAP and "." not in yf and r.on_brief:
            tok = _name_token(r.name)
            if tok:
                prev = tok_best.get(tok)
                if not prev or (r.cap or 0) > prev[0]:   # keep the largest-cap holder of the token
                    tok_best[tok] = (r.cap or 0, yf, r.name)
    tick_map = {k: (v[0], v[1]) for k, v in tick_tmp.items()}
    tok_map = {k: (v[1], v[2]) for k, v in tok_best.items()}
    return tok_map, tick_map


_EXCH = re.compile(r"\((?:NYSE|NASDAQ|NYSEARCA|AMEX|OTC|CBOE)[:\s]+([A-Za-z]{1,6})\)", re.I)
_CASH = re.compile(r"\$([A-Za-z]{1,6})\b")
_TKWORD = re.compile(r"(?<![A-Za-z])([A-Z]{3,6})\s+(?:stock|shares|stocks)\b")  # >=3 (kills 'AI stocks')


def match_company(title, tok_map, tick_map):
    """Map a headline to a company. Returns (ticker, name, kind, token).
    kind='ticker' (explicit, trusted) or 'name' (cap-gated established-name token,
    needs a proximity check by the caller). (None,None,None,None) if no match."""
    t = title or ""
    for rx in (_EXCH, _CASH, _TKWORD):
        for m in rx.findall(t):
            if m.upper() in tick_map:
                tk, nm = tick_map[m.upper()]
                return tk, nm, "ticker", m
    for w in re.findall(r"[a-z]+", t.lower()):
        if w in tok_map:
            tk, nm = tok_map[w]
            return tk, nm, "name", w
    return None, None, None, None


def _title_is_about(title, ticker, name, tok_map, tick_map):
    """True if `title` is genuinely ABOUT `ticker` (not just a Trump story in its news feed).
    Guards Section B, where a per-ticker scraper pulls tangential articles (an Axon/Trump story
    landed in DELL's feed). Accepts if: match_company resolves to this ticker, OR the ticker's
    first name-token (>=4 chars, e.g. 'dell') appears as a whole word in the title, OR the bare
    ticker symbol appears. Otherwise the headline is about some OTHER company - reject."""
    t = (title or "")
    mtk, _, _, _ = match_company(strip_src(t), tok_map, tick_map)
    if mtk and mtk == ticker:
        return True
    low = t.lower()
    tok = _name_token(name)
    if tok and re.search(r"\b" + re.escape(tok) + r"\b", low):
        return True
    base = (ticker or "").split(".")[0]
    if base and re.search(r"\b" + re.escape(base.lower()) + r"\b", low):
        return True
    return False


def pos_near(title, token, window=45):
    """True if a positive verb sits within `window` chars of the company token -
    ties Trump's praise/buy TO the company (kills 'Tom Brady'/wrap false matches)."""
    low = (title or "").lower()
    i = low.find((token or "").lower())
    if i < 0:
        return False
    seg = low[max(0, i - window): i + len(token) + window]
    return bool(_POS.search(seg))


def pub(e):
    p = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
    return dt.datetime(*p[:6]) if p else None


# PRIMARY sources - read at source so we're early (Google RSS is 2nd/3rd hand and ~2 days late)
PRIMARY_FEEDS = [
    ("wh",    "https://www.whitehouse.gov/presidential-actions/feed/"),  # official EOs, same-day
    ("truth", "https://trumpstruth.org/feed"),                           # his Truth Social posts
]


def _strip_html(s):
    return re.sub(r"<[^>]+>", " ", s or "").strip()


# Truth posts are mostly political; only process ones that actually talk markets/stocks
_MARKET = re.compile(r"\b(stocks?|shares?|bought|buy|buying|invest(?:ing|ed|ment|or|ors|s)?|"
                     r"earnings|tariff\w*|market|nasdaq|dow|portfolio)\b|\$[A-Za-z]{2,5}\b", re.I)


def ingest_primary(conn, tok_map, tick_map, seen):
    """Ingest White House actions + Truth Social posts. These ARE Trump, so no is_trump
    gate - instead require a company/ticker match (else it's political noise). Catches his
    direct 'I bought X' statements at source, ahead of the news cycle. Policy text with no
    company (e.g. a tariff EO) is handled by the policy->beneficiary layer, not here."""
    cur = conn.cursor()
    ins = 0
    for src, url in PRIMARY_FEEDS:
        try:
            f = feedparser.parse(url)
        except Exception as ex:
            print(f"  {src} feed error {str(ex)[:50]}")
            continue
        for e in f.entries:
            raw_title = (getattr(e, "title", "") or "")[:400]
            summary = _strip_html(getattr(e, "summary", "") or "")
            text = (raw_title + ". " + summary)[:1200]
            # Truth post titles are generic ("Post from ..."); show his words instead
            title = (summary[:300] if src == "truth" and summary else raw_title)[:400]
            guid = (getattr(e, "id", None) or getattr(e, "link", "") or "")[:320]
            if not guid or guid in seen:
                continue
            seen.add(guid)
            if src == "truth" and not _MARKET.search(text):
                continue  # skip his political posts; only stock/market ones matter here
            tk, nm, kind, tok = match_company(text, tok_map, tick_map)
            if not tk:
                continue
            if kind == "name" and not pos_near(text, tok):
                continue
            dbex(cur, MERGE, src, title, (getattr(e, "link", "") or "")[:600],
                        pub(e), tk, nm, True, sentiment(text), guid)
            ins += 1
        conn.commit()
    return ins


# ============================================================================
# PHASE 2: policy -> beneficiary tickers (a Trump/WH policy theme that moves a sector,
# even when no single company is named). Premium cross = a beneficiary he also OWNS.
# ============================================================================
POLICY_MAP = [
    ("steel",          r"\bsteel\b",                                  ["CLF", "NUE", "STLD"]),
    ("aluminum",       r"alumin",                                     ["AA", "CENX"]),
    ("copper",         r"\bcopper\b",                                 ["FCX", "SCCO"]),
    ("farm machinery", r"\bfarm|agricultur|tractor|machinery|combine\b", ["DE", "AGCO", "CNH", "TITN"]),
    ("autos",          r"\bauto\b|automobile|vehicle",                ["F", "GM", "PCAR"]),
    ("defense",        r"defen[sc]e|military|missile|warship|shipbuild", ["LMT", "RTX", "NOC", "GD", "HII"]),
    ("energy",         r"\boil\b|drilling|petroleum|\bcrude\b|\blng\b", ["XOM", "CVX"]),
    ("aerospace",      r"aircraft|aviation|aerospace",                ["BA", "RTX", "GD"]),
    ("construction",   r"infrastructure|heavy equipment",             ["CAT", "DE", "PCAR"]),
]
POLICY_DDL = """
IF OBJECT_ID('tbl_eb_policy_signal','U') IS NULL
CREATE TABLE tbl_eb_policy_signal (
  id INT IDENTITY PRIMARY KEY, source VARCHAR(10), theme NVARCHAR(40),
  policy_title NVARCHAR(400), link NVARCHAR(600), published DATETIME2,
  ticker NVARCHAR(20), is_holding BIT NOT NULL DEFAULT 0, alerted BIT NOT NULL DEFAULT 0,
  guid VARCHAR(320), fetched_on DATETIME2 NOT NULL DEFAULT now(),
  CONSTRAINT UQ_eb_policy UNIQUE (guid, ticker));
"""
POLICY_MERGE = """
INSERT INTO tbl_eb_policy_signal (source,theme,policy_title,link,published,ticker,is_holding,guid)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
  ON CONFLICT (guid, ticker) DO NOTHING
"""


def trump_holdings(conn):
    """His disclosed buys, derived from our OWN BUY-tier rows (self-maintaining list)."""
    cur = conn.cursor()
    dbex(cur, f"SELECT DISTINCT matched_ticker FROM tbl_eb_pump_news "
                f"WHERE in_universe=true AND {_buytier_sql()}")
    return {r.matched_ticker for r in cur.fetchall()}


# only treat a document as a market catalyst if it's an actual trade/tariff action -
# excludes nominations, commemorative days, etc. that merely mention a theme word
_POLICY_ACTION = re.compile(r"tariff|\bdut(?:y|ies)\b|\bimports?\b|\bexports?\b|\btrade\b|"
                            r"sanction|\bquota\b|\blevy\b|section 232|section 301", re.I)


def detect_policy(text):
    t = (text or "").lower()
    if not _POLICY_ACTION.search(t):
        return []                      # not a trade/tariff action - no sector catalyst
    return [(theme, tickers) for theme, rx, tickers in POLICY_MAP if re.search(rx, t)]


def scan_policy(conn):
    """Scan WH actions for policy themes -> beneficiary tickers (in universe). Flags any
    beneficiary Trump also owns. Returns count inserted."""
    cur = conn.cursor()
    holdings = trump_holdings(conn)
    dbex(cur, "SELECT yf_ticker FROM tbl_eb_universe WHERE active=true")
    univ = {r.yf_ticker for r in cur.fetchall()}
    ins = 0
    try:
        f = feedparser.parse("https://www.whitehouse.gov/presidential-actions/feed/")
    except Exception:
        return 0
    for e in f.entries:
        title = (getattr(e, "title", "") or "")[:400]
        text = title + " " + _strip_html(getattr(e, "summary", "") or "")
        guid = (getattr(e, "id", None) or getattr(e, "link", "") or "")[:320]
        link = (getattr(e, "link", "") or "")[:600]
        if not guid:
            continue
        for theme, tickers in detect_policy(text):
            for tk in tickers:
                if tk not in univ:
                    continue
                dbex(cur, POLICY_MERGE, "wh", theme, title, link, pub(e),
                            tk, tk in holdings, guid)
                ins += 1
    conn.commit()
    return ins


def send_policy_alerts(conn):
    """Premium alert: a Trump trade/policy action that benefits a sector AND he personally
    owns a name in it (the Deere self-dealing pattern). 3-day per-ticker cooldown."""
    cur = conn.cursor()
    win = "is_holding=true AND alerted=false AND published >= (now() - interval '3 days')"
    dbex(cur, f"SELECT theme, ticker, policy_title, link FROM tbl_eb_policy_signal WHERE {win}")
    rows = cur.fetchall()
    if not rows:
        return []
    seen, blocks = set(), []
    for r in rows:
        if r.ticker in seen:
            continue
        seen.add(r.ticker)
        c2 = conn.cursor()
        dbex(c2, "SELECT name FROM tbl_eb_universe WHERE yf_ticker=%s", r.ticker)
        row = c2.fetchone()
        nm = row.name if row else r.ticker
        head = f"{html.escape(nm)} ({r.ticker}) - Trump owns this, and his {html.escape(r.theme)} action benefits it:"
        pol = html.escape(r.policy_title or "")
        if r.link:
            pol += f' <a href="{html.escape(r.link, quote=True)}">[link]</a>'
        parts = [head, pol]
        for tf_label, tf in (("Weekly Wave", "1wk"), ("Daily Wave", "1d")):
            wl = wave_text(tf_label, r.ticker, tf)
            if wl:
                parts.append(html.escape(wl))
        blocks.append("<p>" + "<br>".join(parts) + "</p>")
    subj = "Policy Beneficiary Alert: " + ", ".join(sorted(seen))
    if not send_alert(subj, "".join(blocks)):
        return []
    dbex(cur, f"UPDATE tbl_eb_policy_signal SET alerted=true WHERE {win}")
    conn.commit()
    return sorted(seen)


def send_pending_alerts(conn):
    """Email first-seen BUY-tier (Trump position) positive mentions; one block per ticker
    with the company name + link. Marks rows alerted only on successful send. Returns the
    list of alerted tickers (reusable: called by main and for manual resends)."""
    cur = conn.cursor()
    where = (f"in_universe=true AND sentiment='positive' AND alerted=false AND {_buytier_sql()} "
             f"AND published >= (now() - interval '2 days')")
    dbex(cur, f"SELECT matched_ticker, matched_name, title, link FROM tbl_eb_pump_news WHERE {where}")
    rows = cur.fetchall()
    if not rows:
        return []
    # per-ticker cooldown: a ticker alerts AT MOST ONCE PER MONTH (spec 01/07/2026). A recycled
    # narrative (e.g. the Trump-Dell-stake / Pentagon story rewritten by outlet after outlet for
    # weeks) was re-pumping Dell repeatedly - the 3-day window let the same event re-alert. 30 days
    # means one pump per name per catalyst-window; a genuinely NEW catalyst a month later re-alerts.
    COOLDOWN_DAYS = 30
    dbex(cur, """SELECT DISTINCT matched_ticker FROM tbl_eb_pump_news
                   WHERE alerted=true AND fetched_on >= (now() - make_interval(days => %s))""",
         COOLDOWN_DAYS)
    cooldown = {r.matched_ticker for r in cur.fetchall()}
    FRESH_DAYS = 7   # spec 10/06/2026: nothing surfaces unless provably <= 7 days old
    now = dt.datetime.utcnow()
    seen_t, blocks, names = set(), [], []
    for h in rows:
        if h.matched_ticker in seen_t or h.matched_ticker in cooldown:
            continue
        # spec freshness rule: verify the REAL article date (Google re-dates recycled
        # stories). If the article won't give up its date, try an ALTERNATIVE source for
        # the same story. If nothing can be verified, the story is binned, not guessed.
        real = article_date(h.link) or alt_source_date(h.title)
        if real is None:
            print(f"  unverified-bin {h.matched_ticker}: no provable date for '{(h.title or '')[:48]}'")
            continue
        dbex(cur, "UPDATE tbl_eb_pump_news SET published=%s, date_verified=true WHERE link=%s",
             real, h.link)
        conn.commit()
        if (now - real).days > FRESH_DAYS:
            print(f"  stale-skip {h.matched_ticker}: article dated {real:%Y-%m-%d}")
            continue
        seen_t.add(h.matched_ticker)
        c2 = conn.cursor()
        dbex(c2, "SELECT 1 FROM tbl_eb_pool WHERE yf_ticker=%s AND fit='strong'", h.matched_ticker)
        sf = " (Early Bird Strong Match)" if c2.fetchone() else ""
        label = h.matched_name or h.matched_ticker      # company name, e.g. "TKO Group Holdings"
        names.append(label)
        title_line = html.escape(h.title or "")
        if h.link:
            title_line += f' <a href="{html.escape(h.link, quote=True)}">[link]</a>'
        parts = [f"{html.escape(label)}{sf}:", title_line]
        for tf_label, tf in (("Weekly Wave", "1wk"), ("Daily Wave", "1d")):
            wl = wave_text(tf_label, h.matched_ticker, tf)   # self-contained WaveTrend from data
            if wl:
                parts.append(html.escape(wl))
        blocks.append("<p>" + "<br>".join(parts) + "</p>")
    if blocks:
        subj = "Stock Pump: " + ", ".join(sorted(names))
        body = "".join(blocks)
        if not send_alert(subj, body):
            return []  # send failed - leave rows unalerted so it retries next run
    # mark ALL candidate rows handled (emailed + cooldown-suppressed) so dupes don't linger
    dbex(cur, f"UPDATE tbl_eb_pump_news SET alerted=true WHERE {where}")
    conn.commit()
    return sorted(seen_t)


def verify_recent(conn, limit=12):
    """Verify publish dates for recent positive mentions so the weekly brief can show
    only provably fresh items (date_verified=true). First-hand sources (White House,
    Truth Social, our own per-ticker news feeds) are trusted as-is; Google News items
    must prove their date - directly or via an alternative source - or stay unverified
    (and therefore never surface)."""
    cur = conn.cursor()
    dbex(cur, """UPDATE tbl_eb_pump_news SET date_verified=true
                 WHERE date_verified=false AND source IN ('wh','truth','pool')""")
    conn.commit()
    dbex(cur, """SELECT id, title, link FROM tbl_eb_pump_news
                 WHERE source='google' AND date_verified=false AND in_universe=true
                   AND sentiment='positive' AND published >= now() - interval '7 days'
                 ORDER BY published DESC LIMIT %s""", limit)
    rows = cur.fetchall()
    n_ok = 0
    for r in rows:
        real = article_date(r.link) or alt_source_date(r.title)
        if real is None:
            continue
        dbex(cur, "UPDATE tbl_eb_pump_news SET published=%s, date_verified=true WHERE id=%s",
             real, r.id)
        n_ok += 1
    conn.commit()
    if rows:
        print(f"  date-verified {n_ok}/{len(rows)} recent google items")


def scan_figures(conn, tok_map, tick_map, seen):
    """Discovery for the non-Trump pumpers (Huang, hyperscaler CEOs, Altman, Su).
    Same matching + sentiment as Trump, plus the curated alias map for clean
    'figure backs Company' headlines. Sets the `figure` column. Returns count inserted."""
    cur = conn.cursor()
    ins = 0
    for person in PEOPLE:
        for url in figure_feeds(person):
            try:
                f = feedparser.parse(url)
            except Exception as ex:
                print(f"  figure feed error {str(ex)[:50]}"); continue
            for e in f.entries:
                title = (getattr(e, "title", "") or "")[:400]
                fig = which_figure(title)
                if not fig or fig == "trump":      # Trump handled by his own pipeline
                    continue
                guid = (getattr(e, "id", None) or getattr(e, "link", "") or "")[:320]
                if not guid or guid in seen:
                    continue
                seen.add(guid)
                mtitle = strip_src(title)
                if sentiment(mtitle) != "positive" or is_wrap(mtitle):
                    continue
                tk, nm, kind, tok = match_company(mtitle, tok_map, tick_map)
                ok = tk and (kind == "ticker" or (kind == "name" and pos_near(mtitle, tok)))
                if not ok:
                    atk, anm = alias_match(mtitle, fig)
                    if atk:
                        tk, nm, ok = atk, anm, True
                if ok:
                    dbex(cur, FIGURE_MERGE, fig, "google", title, (getattr(e, "link", "") or "")[:600],
                         pub(e), tk, nm, True, "positive", guid)
                    ins += 1
            conn.commit()
    return ins


def main():
    print(f"== stock-pumps scan {dt.datetime.now():%Y-%m-%d %H:%M:%S} ==", flush=True)
    conn = get_conn(); cur = conn.cursor()
    tok_map, tick_map = build_matcher(cur)
    seen, ins = set(), 0

    # ---- A) DISCOVERY: Google News ----
    for url in GNEWS:
        try:
            f = feedparser.parse(url)
        except Exception as ex:
            print(f"  GNEWS error {str(ex)[:50]}"); continue
        for e in f.entries:
            title = (getattr(e, "title", "") or "")[:400]
            if not is_trump(title):
                continue
            guid = (getattr(e, "id", None) or getattr(e, "link", "") or "")[:320]
            if not guid or guid in seen:
                continue
            seen.add(guid)
            mtitle = strip_src(title)               # match on the headline, not the publisher tag
            tk, nm, kind, tok = match_company(mtitle, tok_map, tick_map)
            sent = sentiment(mtitle)
            # name matches must be non-wrap AND have the positive verb near the company;
            # explicit ticker matches are trusted as-is
            name_ok = kind == "name" and not is_wrap(mtitle) and pos_near(mtitle, tok)
            if tk and (kind == "ticker" or name_ok):
                dbex(cur, MERGE, "google", title, (getattr(e,"link","") or "")[:600],
                            pub(e), tk, nm, True, sent, guid)
                ins += 1
            elif not tk and sent == "positive" and not is_wrap(mtitle):
                # "anything he mentions" - log positive un-mappable mentions too (not in digest)
                dbex(cur, MERGE, "google", title, (getattr(e,"link","") or "")[:600],
                            pub(e), '', None, False, sent, guid)
                ins += 1
        conn.commit()

    # ---- A2) PRIMARY: White House actions + Truth Social posts (read at source = early) ----
    ins += ingest_primary(conn, tok_map, tick_map, seen)

    # ---- A3) POLICY -> beneficiary tickers (sector catalysts even when no company named) ----
    pol = scan_policy(conn)
    if pol:
        print(f"  policy beneficiaries: +{pol}", flush=True)

    # ---- B) PRECISION: Trump mentions inside news we already collect ----
    dbex(cur, """SELECT n.yf_ticker, n.title, n.url, n.published, u.name
                   FROM tbl_eb_news n JOIN tbl_eb_universe u ON u.yf_ticker=n.yf_ticker
                   WHERE n.title ILIKE '%Trump%' AND n.published >= (now() - interval '3 days')
                   ORDER BY n.published DESC LIMIT 400""")
    for r in cur.fetchall():
        if not is_trump(r.title) or is_wrap(r.title):
            continue  # require the proper noun and drop generic index/market-wrap headlines
        # VERIFY the headline is actually ABOUT this ticker, not just a Trump story that landed in
        # its news feed. A per-ticker scraper pulls tangential "Trump trades" articles - e.g. an
        # AXON headline ("Trump bought Taser maker Axon") appeared in DELL's feed and was blindly
        # tagged DELL. Require the ticker's own name-token OR an explicit ticker match in the title.
        if not _title_is_about(r.title, r.yf_ticker, r.name, tok_map, tick_map):
            continue
        guid = ("pool:" + (r.url or (r.yf_ticker + r.title))[:300])[:320]
        if guid in seen:
            continue
        seen.add(guid)
        dbex(cur, MERGE, "pool", (r.title or "")[:400], (r.url or "")[:600],
                    r.published, r.yf_ticker, r.name, True, sentiment(r.title), guid)
        ins += 1
    conn.commit()

    # ---- C) THE OTHER PUMPERS: Huang, hyperscaler CEOs, Altman, Su ----
    ins += scan_figures(conn, tok_map, tick_map, seen)

    dbex(cur, """SELECT COUNT(*) n,
                   SUM(CASE WHEN in_universe=true AND sentiment='positive' THEN 1 ELSE 0 END) pos
                   FROM tbl_eb_pump_news""")
    row = cur.fetchone()
    print(f"stock_pumps: +{ins} this run | {row.n} total, {row.pos} positive & mapped")
    verify_recent(conn)
    alerted = send_pending_alerts(conn)
    if alerted:
        print(f"  ALERT emailed: {', '.join(alerted)}")
    palerted = send_policy_alerts(conn)
    if palerted:
        print(f"  POLICY+HOLDING ALERT emailed: {', '.join(palerted)}")
    conn.close()


def recent(conn, days=3):
    """For the digest: ESTABLISHED names Trump spoke POSITIVELY about, most recent first."""
    cur = conn.cursor()
    dbex(cur, """SELECT matched_ticker, matched_name, source, LEFT(title,60) t, published
                   FROM tbl_eb_pump_news
                   WHERE in_universe=true AND sentiment='positive'
                     AND published >= (now() - (%s * interval '1 day'))
                   ORDER BY published DESC LIMIT 12""", days)
    return cur.fetchall()


if __name__ == "__main__":
    main()
