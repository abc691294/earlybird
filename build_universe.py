"""
build_universe.py - build/refresh tbl_eb_universe (Postgres/Supabase): one clean,
tradable row per company.

Source: the T212 instruments scrape (instruments_cache.json, produced by the T212 Quant
Pie project). For each company (keyed by ISIN) T212 may list several lines (a US primary
plus illiquid German cross-listings, etc.). We pick ONE primary listing per ISIN by
exchange desirability, so we screen the liquid line with good Yahoo data.

Re-runnable: this IS the maintenance process. Upserts on ISIN - existing rows refreshed,
new listings inserted, and anything no longer offered marked active=false.

Ported from the SQL-Server original (Stock Research) to Supabase Postgres. Runs LOCALLY as
a weekly task (it reads the local instruments cache); writes to the cloud universe the rest
of the engine screens. Pair it with a cache refresh first (python t212_client.py in the
Quant Pie project) so the cache is fresh before this rebuilds from it.

Usage:  python build_universe.py [path_to_instruments_cache.json]
"""
import json
import sys
import collections
import datetime as dt
from t212_yf_map import resolve_yf, country_of
from eb_db import get_conn

CACHE = (sys.argv[1] if len(sys.argv) > 1
         else r"C:\Users\sbrow\OneDrive\Claude\projects\T212 Quant Pie\instruments_cache.json")

# Lower = more desirable listing. .DU (Dusseldorf) cross-listings are last resort.
EXCH_PRIORITY = {
    "": 0, ".L": 1, ".TO": 2, ".AX": 3, ".PA": 4, ".DE": 5, ".AS": 6, ".MI": 7,
    ".MC": 8, ".ST": 9, ".SW": 10, ".CO": 11, ".OL": 12, ".BR": 13, ".LS": 14,
    ".VI": 15, ".HK": 16, ".DU": 99,
}


def suffix_of(yf):
    return "." + yf.split(".")[-1] if "." in yf else ""


def pick_primary(listings):
    """Choose the best listing for one ISIN."""
    scored = []
    for d in listings:
        yf = resolve_yf(d)
        scored.append((EXCH_PRIORITY.get(suffix_of(yf), 50), yf, d))
    scored.sort(key=lambda x: x[0])
    return scored[0][1], scored[0][2]


def main():
    data = json.load(open(CACHE, encoding="utf-8"))
    stocks = [d for d in data if d.get("type") == "STOCK" and d.get("isin")]
    by_isin = collections.defaultdict(list)
    for d in stocks:
        by_isin[d["isin"]].append(d)

    rows = []
    for isin, listings in by_isin.items():
        yf, chosen = pick_primary(listings)
        ctry = country_of(chosen)
        foreign = (suffix_of(yf) == ".DU"
                   or (ctry and ctry != isin[:2] and isin[:2].isalpha()))
        rows.append((isin, yf, chosen["ticker"], (chosen.get("name") or "")[:200],
                     ctry, chosen.get("currencyCode"), chosen.get("type"), bool(foreign)))
    print(f"{len(stocks)} stock listings -> {len(rows)} unique companies "
          f"({sum(1 for r in rows if r[7])} flagged only_foreign/thin)")

    now = dt.datetime.now(dt.timezone.utc)
    conn = get_conn()
    cur = conn.cursor()

    # upsert on ISIN (the PK). Existing rows refreshed + reactivated; new ones inserted.
    cur.executemany(
        """INSERT INTO tbl_eb_universe
             (isin, yf_ticker, t212_ticker, name, country, currency, type,
              only_foreign_listing, active, first_seen, last_seen)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s)
           ON CONFLICT (isin) DO UPDATE SET
             yf_ticker=EXCLUDED.yf_ticker, t212_ticker=EXCLUDED.t212_ticker,
             name=EXCLUDED.name, country=EXCLUDED.country, currency=EXCLUDED.currency,
             type=EXCLUDED.type, only_foreign_listing=EXCLUDED.only_foreign_listing,
             active=true, last_seen=EXCLUDED.last_seen""",
        [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], now, now) for r in rows])

    # NOT MATCHED BY SOURCE: anything no longer in the scrape -> active=false (delisted/removed).
    seen = [r[0] for r in rows]
    cur.execute(
        "UPDATE tbl_eb_universe SET active=false, last_seen=%s "
        "WHERE active=true AND isin <> ALL(%s)", (now, seen))
    deactivated = cur.rowcount
    conn.commit()

    cur.execute("SELECT COUNT(*) tot, COUNT(*) FILTER (WHERE active) act, "
                "COUNT(*) FILTER (WHERE only_foreign_listing) frn FROM tbl_eb_universe")
    r = cur.fetchone()
    print(f"tbl_eb_universe: {r.tot} rows, {r.act} active, {r.frn} flagged "
          f"({deactivated} newly deactivated - no longer offered)")
    conn.close()


if __name__ == "__main__":
    main()
