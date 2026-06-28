"""supply_confirm.py - keep or drop a discovered supply-chain candidate link in one command.

The weekly brief lists borderline supplier->customer links the engine auto-discovered from the news
(source='discover-candidate'). The clear ones (both ends on-brief) are auto-confirmed already; these
are the ones left for your call. This is the one-line lever - no raw SQL.

  python supply_confirm.py TSM AMKR          # CONFIRM: promote the candidate to a trusted map link
  python supply_confirm.py --drop GM LMT     # DROP: delete the candidate (off-brief / wrong)
  python supply_confirm.py --list            # show all pending candidates

Confirming sets source='auto' (a trusted link, same as a hand-seeded one). Dropping deletes it.
Doing neither leaves it a candidate - read by supply.py but flagged as unconfirmed, so it is safe to
ignore. Only ever touches source='discover-candidate' rows: a confirmed/hand-seeded link is never
altered by this tool.
"""
import sys
from eb_db import get_conn, dbex


def _list(cur):
    dbex(cur, """SELECT theme, upstream, downstream, role FROM tbl_eb_supply_link
                 WHERE source='discover-candidate' ORDER BY theme, upstream""")
    rows = cur.fetchall()
    if not rows:
        print("No pending candidates.")
        return
    print(f"{len(rows)} pending candidate link(s):")
    for r in rows:
        print(f"  [{r.theme}] {r.upstream} -> {r.downstream}  ({r.role or ''})")


def main():
    args = sys.argv[1:]
    conn = get_conn()
    cur = conn.cursor()
    if not args or args[0] == "--list":
        _list(cur); conn.close(); return

    drop = args[0] == "--drop"
    if drop:
        args = args[1:]
    if len(args) != 2:
        print(__doc__); conn.close(); return
    up, down = args[0].upper(), args[1].upper()

    if drop:
        # Drop a DISCOVERED link (candidate OR an auto-confirmed one that turned out off-brief).
        # Discovered links carry 'from news' in the note; hand-seeded links never do, so they are
        # never deleted by this tool. This lets you undo a bad auto-confirm with one command.
        dbex(cur, """DELETE FROM tbl_eb_supply_link
                     WHERE note LIKE '%%from news%%'
                       AND upper(upstream)=%s AND upper(downstream)=%s""",
             up, down)
        msg = "dropped" if cur.rowcount else "no matching discovered link"
    else:
        dbex(cur, """UPDATE tbl_eb_supply_link SET source='auto'
                     WHERE source='discover-candidate' AND upper(upstream)=%s AND upper(downstream)=%s""",
             up, down)
        msg = "confirmed (now a trusted map link)" if cur.rowcount else "no matching candidate"
    conn.commit()
    print(f"{up} -> {down}: {msg}.")
    conn.close()


if __name__ == "__main__":
    main()
