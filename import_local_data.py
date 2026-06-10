"""
import_local_data.py - ONE-TIME migration of the last local JSON files into Supabase,
so Supabase becomes the single source of truth.

Loads:
  - history.json per-name records   -> tbl_eb_assessment (dated scoring trail)
  - history.json _watchlist         -> tbl_eb_watchlist (already seeded; refreshed here)
  - strategic_stakes.json           -> tbl_eb_stake (investor stakes, listed picks, watch)

Idempotent (upserts on natural keys), so it is safe to re-run. After this, the JSON
files are backups only - nothing live reads them.

Usage: python import_local_data.py [path-to-Stock-Research-folder]
"""
import json
import sys
from pathlib import Path
from eb_db import get_conn, dbex

ASSESS_UPSERT = """
INSERT INTO tbl_eb_assessment (sym, as_of, price, day_pct, wk_pct, range_pct, flag,
                               target, upside, rec, qoq, yoy, stance, score, changed)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (sym, as_of) DO UPDATE SET
  price=EXCLUDED.price, day_pct=EXCLUDED.day_pct, wk_pct=EXCLUDED.wk_pct,
  range_pct=EXCLUDED.range_pct, flag=EXCLUDED.flag, target=EXCLUDED.target,
  upside=EXCLUDED.upside, rec=EXCLUDED.rec, qoq=EXCLUDED.qoq, yoy=EXCLUDED.yoy,
  stance=EXCLUDED.stance, score=EXCLUDED.score, changed=EXCLUDED.changed
"""

STAKE_UPSERT = """
INSERT INTO tbl_eb_stake (category, investor, target, size, stake_type, thesis,
                          valuation, listed_ticker, is_private)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (category, investor, target) DO UPDATE SET
  size=EXCLUDED.size, stake_type=EXCLUDED.stake_type, thesis=EXCLUDED.thesis,
  valuation=EXCLUDED.valuation, listed_ticker=EXCLUDED.listed_ticker,
  is_private=EXCLUDED.is_private, updated_on=now()
"""


def _num(v):
    return v if isinstance(v, (int, float)) else None


def import_assessments(cur, hist):
    n = 0
    for sym, recs in hist.items():
        if sym.startswith("_") or not isinstance(recs, dict):
            continue
        for date, r in recs.items():
            if not isinstance(r, dict):
                continue
            dbex(cur, ASSESS_UPSERT, sym, date, _num(r.get("price")), _num(r.get("day_pct")),
                 _num(r.get("wk_pct")), _num(r.get("range")), r.get("flag"), _num(r.get("target")),
                 _num(r.get("upside")), r.get("rec"), _num(r.get("qoq")), _num(r.get("yoy")),
                 r.get("stance"), _num(r.get("score")), r.get("changed"))
            n += 1
    return n


def import_stakes(cur, stakes):
    n = 0
    # investor -> {target -> {...}} blocks (skip _README/_updated and inline _capex notes)
    for investor, block in stakes.items():
        if investor.startswith("_") or not isinstance(block, dict):
            continue
        for target, d in block.items():
            if target.startswith("_") or not isinstance(d, dict):
                continue
            dbex(cur, STAKE_UPSERT, "investor_stake", investor, target,
                 d.get("size"), d.get("type"), d.get("thesis"), d.get("valuation"), None, True)
            n += 1
    # listed picks-and-shovels (ticker -> one-line note)
    for tk, note in stakes.get("_listed_picks_and_shovels", {}).items():
        dbex(cur, STAKE_UPSERT, "listed_pick", "", tk, None, None, note, None, tk, False)
        n += 1
    # other private to watch
    for target, d in stakes.get("OTHER_PRIVATE_TO_WATCH", {}).items():
        if not isinstance(d, dict):
            continue
        dbex(cur, STAKE_UPSERT, "private_to_watch", "", target,
             d.get("size"), d.get("type"), d.get("thesis"), d.get("valuation"), None, True)
        n += 1
    return n


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parent.parent / "Stock Research"
    hist = json.loads((folder / "history.json").read_text(encoding="utf-8"))
    stakes = json.loads((folder / "strategic_stakes.json").read_text(encoding="utf-8"))
    conn = get_conn()
    cur = conn.cursor()
    a = import_assessments(cur, hist)
    s = import_stakes(cur, stakes)
    conn.commit()
    dbex(cur, "SELECT COUNT(*) n FROM tbl_eb_assessment")
    ta = cur.fetchone().n
    dbex(cur, "SELECT COUNT(*) n FROM tbl_eb_stake")
    ts = cur.fetchone().n
    print(f"assessments: +{a} this run, {ta} total")
    print(f"stakes:      +{s} this run, {ts} total")
    conn.close()


if __name__ == "__main__":
    main()
