"""
sa_13f.py - track Situational Awareness LP (Aschenbrenner) 13F buys/sells.

His fund's thesis (AGI + power bottleneck) = ours, and a conviction buy from him is a
high-quality co-sign. 13Fs are quarterly + delayed ~45 days, so this is a CONFIRMATION
signal (his names worth watching), not an early one - and it only shows long US positions.

Pulls his 13F holdings from SEC EDGAR into tbl_eb_sa_13f, diffs the two latest quarters
(NEW buys / exits / adds / trims), and cross-refs to our universe. Run weekly.
"""
import urllib.request, json, re, time
from eb_db import get_conn, dbex

HDR = {"User-Agent": "EarlyBird Research research@example.com"}
FUNDS = {"2038540": "Situational Awareness Partners LP", "2045724": "Situational Awareness LP",
         "1088875": "Baillie Gifford", "1697748": "ARK Invest",
         "1135730": "Coatue", "1387322": "Whale Rock"}
# conviction ACTIVE managers only. Deliberately NOT the passive index giants
# (Vanguard/BlackRock/State Street) - they hold everything by mandate, zero signal.

DDL = """
IF OBJECT_ID('tbl_eb_sa_13f','U') IS NULL
CREATE TABLE tbl_eb_sa_13f (
  cik VARCHAR(12) NOT NULL, fund NVARCHAR(80) NULL, period DATE NOT NULL,
  filed DATE NULL, name NVARCHAR(150) NOT NULL, cusip VARCHAR(12) NULL,
  value BIGINT NULL, shares BIGINT NULL,
  CONSTRAINT UQ_sa13f UNIQUE (cik, period, name, cusip));
"""


def get(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers=HDR), timeout=25).read()


def holdings_for(cik, acc):
    fi = json.loads(get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"))
    for it in fi["directory"]["item"]:
        if not it["name"].endswith(".xml"):
            continue
        raw = get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{it['name']}").decode("utf-8", "ignore")
        if "infoTable" not in raw:
            continue
        out = []
        for b in re.findall(r"<(?:\w+:)?infoTable>(.*?)</(?:\w+:)?infoTable>", raw, re.S):
            nm = re.search(r"nameOfIssuer>(.*?)<", b)
            cu = re.search(r"cusip>(.*?)<", b)
            v = re.search(r"value>(\d+)", b)
            sh = re.search(r"sshPrnamt>(\d+)", b)
            if nm:
                out.append((nm.group(1).strip()[:150], (cu.group(1).strip() if cu else None),
                            int(v.group(1)) if v else 0, int(sh.group(1)) if sh else 0))
        return out
    return []


def load(conn):
    cur = conn.cursor()
    for cik, fund in FUNDS.items():
        sub = json.loads(get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"))
        rec = sub["filings"]["recent"]
        got = 0
        for i, form in enumerate(rec["form"]):
            if not form.startswith("13F-HR") or got >= 2:
                continue
            acc = rec["accessionNumber"][i].replace("-", "")
            period, filed = rec["reportDate"][i], rec["filingDate"][i]
            for nm, cu, v, sh in holdings_for(cik, acc):
                dbex(cur, """INSERT INTO tbl_eb_sa_13f (cik,fund,period,filed,name,cusip,value,shares)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (cik,period,name,cusip) DO NOTHING""",
                    cik, fund, period, filed, nm, (cu or ''), v, sh)
            got += 1; conn.commit(); time.sleep(0.2)
    print("loaded 13F holdings")


def _isin_check(body):
    s = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in body)
    total, dbl = 0, True
    for ch in reversed(s):
        d = int(ch)
        if dbl:
            d *= 2
            if d > 9:
                d -= 9
        total += d; dbl = not dbl
    return str((10 - total % 10) % 10)


def isin_from_cusip(cusip):
    """US 13F CUSIP -> ISIN (US + CUSIP + check digit), to join our ISIN-keyed universe."""
    if not cusip or len(cusip) != 9:
        return None
    body = "US" + cusip.upper()
    return body + _isin_check(body)


def _ticker_map(cur, cusips):
    m = {}
    for cu in cusips:
        isin = isin_from_cusip(cu)
        if not isin:
            continue
        dbex(cur, "SELECT yf_ticker, only_foreign_listing FROM tbl_eb_universe WHERE isin=%s", isin)
        r = cur.fetchone()
        if r:
            m[cu] = (r.yf_ticker, r.only_foreign_listing)
    return m


def comparable(conn, cik="2045724"):
    """Position-over-time: shares held per quarter per name, + our tradable ticker."""
    cur = conn.cursor()
    dbex(cur, "SELECT name, cusip, period, shares, value FROM tbl_eb_sa_13f WHERE cik=%s", cik)
    rows = cur.fetchall()
    periods = sorted({r.period for r in rows})
    by, cusip_of = {}, {}
    for r in rows:
        d = by.setdefault(r.name, {})
        d[r.period] = d.get(r.period, 0) + (r.shares or 0)  # sum multiple lots
        if r.cusip:
            cusip_of[r.name] = r.cusip
    tmap = _ticker_map(cur, set(cusip_of.values()))
    latest = periods[-1]
    dbex(cur, "SELECT name, SUM(value) v FROM tbl_eb_sa_13f WHERE cik=%s AND period=%s GROUP BY name", cik, latest)
    order = {r.name: r.v for r in cur.fetchall()}
    names = sorted(by, key=lambda n: -order.get(n, 0))
    print(f"\n{FUNDS[cik]} - holdings over time (shares 000s); * = thin foreign listing\n")
    print("  " + f"{'name':27} {'buy?':8} " + " ".join(f"{str(p)[2:]:>8}" for p in periods) + "  trend")
    for n in names:
        tk, frn = tmap.get(cusip_of.get(n, ""), (None, None))
        tag = (tk + ("*" if frn else "")) if tk else "-"
        seq = [by[n].get(p, 0) for p in periods]
        cells = " ".join((f"{s/1000:>8.0f}" if s else f"{'-':>8}") for s in seq)
        fnz = next((x for x in seq if x), 0)
        trend = ("NEW" if seq[0] == 0 and seq[-1] > 0 else "EXIT" if seq[-1] == 0 and any(seq)
                 else "build" if seq[-1] > fnz * 1.1 else "trim" if 0 < seq[-1] < fnz * 0.9 else "hold")
        print(f"  {n[:27]:27} {tag:8} {cells}  {trend}")


def diff(conn, cik="2038540"):
    cur = conn.cursor()
    dbex(cur, "SELECT DISTINCT period FROM tbl_eb_sa_13f WHERE cik=%s ORDER BY period DESC", cik)
    periods = [r.period for r in cur.fetchall()]
    if len(periods) < 2:
        print("need 2 periods to diff; only have", periods); return
    cur_p, prev_p = periods[0], periods[1]
    print(f"\n{FUNDS[cik]} - buys/sells {prev_p} -> {cur_p}\n")
    dbex(cur, """
      WITH c AS (SELECT name, SUM(value) v, SUM(shares) s FROM tbl_eb_sa_13f WHERE cik=%s AND period=%s GROUP BY name),
           p AS (SELECT name, SUM(value) v, SUM(shares) s FROM tbl_eb_sa_13f WHERE cik=%s AND period=%s GROUP BY name)
      SELECT COALESCE(c.name,p.name) name, c.s cur_sh, p.s prev_sh, c.v cur_val,
        CASE WHEN p.name IS NULL THEN 'NEW BUY' WHEN c.name IS NULL THEN 'EXITED'
             WHEN c.s>p.s*1.1 THEN 'ADDED' WHEN c.s<p.s*0.9 THEN 'TRIMMED' ELSE 'held' END act
      FROM c FULL JOIN p ON c.name=p.name
      WHERE p.name IS NULL OR c.name IS NULL OR ABS(COALESCE(c.s,0)-COALESCE(p.s,0)) > p.s*0.1
      ORDER BY CASE WHEN p.name IS NULL THEN 0 WHEN c.name IS NULL THEN 1 ELSE 2 END, c.v DESC""",
      cik, cur_p, cik, prev_p)
    rows = cur.fetchall()
    # cross-ref to our universe by loose name match
    for r in rows:
        key = re.split(r"[ ,]", r.name)[0].lower()
        dbex(cur, "SELECT yf_ticker FROM tbl_eb_universe WHERE LOWER(name) LIKE %s LIMIT 1", key + "%")
        u = cur.fetchone()
        tag = f"[{u.yf_ticker}]" if u else "[not in univ]"
        sh = f"{(r.cur_sh or 0):,}" if r.act != 'EXITED' else f"(was {r.prev_sh:,})"
        print(f"  {r.act:8} {r.name[:30]:30} {tag:16} {sh}")


def holders(conn, yf_ticker):
    """Which tracked conviction managers hold a given name (latest filing each)."""
    cur = conn.cursor()
    dbex(cur, "SELECT isin FROM tbl_eb_universe WHERE yf_ticker=%s", yf_ticker)
    r = cur.fetchone()
    if not r or not r.isin or not r.isin.startswith("US"):
        print(f"{yf_ticker}: no US CUSIP to match"); return
    cusip = r.isin[2:11]
    dbex(cur, """SELECT t.fund, MAX(t.period) p,
        (SELECT shares FROM tbl_eb_sa_13f x WHERE x.fund=t.fund AND x.cusip=%s ORDER BY period DESC LIMIT 1) sh
        FROM tbl_eb_sa_13f t WHERE t.cusip=? GROUP BY t.fund""", cusip, cusip)
    rows = cur.fetchall()
    print(f"{yf_ticker} (cusip {cusip}) held by conviction managers:")
    for x in rows:
        print(f"  {x.fund:34} {x.sh:,} sh (as of {x.p})")
    if not rows:
        print("  none of the tracked conviction funds hold it")


if __name__ == "__main__":
    import sys
    c = get_conn()
    if len(sys.argv) > 1 and sys.argv[1] == "holders":
        holders(c, sys.argv[2])
    else:
        load(c); comparable(c, "2045724")
    c.close()
