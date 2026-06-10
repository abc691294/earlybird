"""
push_watchlist.py - one-way sync of the LOCAL watchlist into the cloud database.

The watchlist master lives locally in history.json (key '_watchlist'). The cloud jobs
(weekly brief, buying-moment alerts) read tbl_eb_watchlist, so run this after any local
watchlist change. Local-only utility - the cloud workflows never call it.

Usage:
  python push_watchlist.py [path-to-history.json]
  (default path: ../Stock Research/history.json relative to this repo)

Marks HIGH-priority names and held positions from the small maps below - update those
maps when priorities or holdings change.
"""
import json
import sys
from pathlib import Path
from eb_db import get_conn, dbex

HIGH_PRIORITY = {"BBAI", "CBRS"}
# names with real money in them (update as positions change)
HELD = {"ALMU", "AXTI", "IQE.L", "BURU", "ONDS", "MOB"}
#   ALMU Aeluma, AXTI AXT, IQE.L IQE, BURU Nuburu, ONDS Ondas, MOB Mobilicom.
#   Xanadu (XNDU) deliberately NOT here - see note in HANDOVER; confirm it is the
#   right listing before tracking, as Xanadu Quantum is a private company.

UPSERT = """
INSERT INTO tbl_eb_watchlist (sym, name, sector, priority, held, noted, noted_price,
                              why, flags, triggers, active, updated_on)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,now())
ON CONFLICT (sym) DO UPDATE SET
  name=EXCLUDED.name, sector=EXCLUDED.sector, priority=EXCLUDED.priority,
  held=EXCLUDED.held, noted=EXCLUDED.noted, noted_price=EXCLUDED.noted_price,
  why=EXCLUDED.why, flags=EXCLUDED.flags, triggers=EXCLUDED.triggers,
  active=true, updated_on=now()
"""


def main():
    default = Path(__file__).resolve().parent.parent / "Stock Research" / "history.json"
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    wl = json.loads(path.read_text(encoding="utf-8")).get("_watchlist", [])
    if not wl:
        raise SystemExit(f"No _watchlist found in {path}")
    conn = get_conn()
    cur = conn.cursor()
    syms = set()
    for e in wl:
        sym = e.get("sym")
        if not sym:
            continue
        syms.add(sym)
        dbex(cur, UPSERT, sym, e.get("name"), e.get("sector"),
             "high" if sym in HIGH_PRIORITY else "", sym in HELD,
             e.get("noted"), e.get("noted_price"),
             e.get("why"), e.get("flags"), e.get("triggers"))
    # held names not on the watchlist still belong in the table (the brief reports holdings)
    for sym in HELD - syms:
        dbex(cur, """INSERT INTO tbl_eb_watchlist (sym, held, why) VALUES (%s, true, 'Held position.')
                     ON CONFLICT (sym) DO UPDATE SET held=true, active=true, updated_on=now()""", sym)
        syms.add(sym)
    # anything in the cloud no longer in the local list is deactivated, not deleted
    dbex(cur, "UPDATE tbl_eb_watchlist SET active=false, updated_on=now() WHERE NOT (sym = ANY(%s))",
         list(syms))
    conn.commit()
    dbex(cur, "SELECT COUNT(*) n FROM tbl_eb_watchlist WHERE active=true")
    print(f"watchlist pushed: {len(wl)} from file, {cur.fetchone().n} active in cloud")
    conn.close()


if __name__ == "__main__":
    main()
