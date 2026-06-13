"""supply.py - use the supply-chain map (tbl_eb_supply_link).

  python supply.py NVDA           # who feeds NVDA (its suppliers, by layer)
  python supply.py "AI optical"   # suppliers for a theme/leader label
  python supply.py --upstream LRCX  # what does LRCX feed (where it sits in chains)
  python supply.py --theme Quantum  # the whole Quantum chain

Importable: suppliers_of(conn, target), feeds_of(conn, ticker), theme_chain(conn, theme).
Used by the brief to surface the picks-and-shovels layer when a leader pumps.
"""
import sys
from eb_db import get_conn, dbex


def suppliers_of(conn, target, max_layer=3):
    """Names that feed `target` (a ticker or theme/leader label), nearest layer first."""
    cur = conn.cursor()
    dbex(cur, """SELECT upstream, upstream_name, layer, role, listed, note
                 FROM tbl_eb_supply_link
                 WHERE downstream = %s AND layer <= %s
                 ORDER BY layer, listed DESC, upstream""", target, max_layer)
    return cur.fetchall()


def feeds_of(conn, ticker):
    """Chains `ticker` participates in as a supplier (what it feeds)."""
    cur = conn.cursor()
    dbex(cur, """SELECT theme, downstream, layer, role
                 FROM tbl_eb_supply_link WHERE upstream = %s
                 ORDER BY theme, layer""", ticker)
    return cur.fetchall()


def theme_chain(conn, theme):
    cur = conn.cursor()
    dbex(cur, """SELECT downstream, upstream, upstream_name, layer, role, listed
                 FROM tbl_eb_supply_link WHERE theme = %s
                 ORDER BY layer, downstream, upstream""", theme)
    return cur.fetchall()


def main():
    conn = get_conn()
    args = sys.argv[1:]
    if not args:
        print(__doc__); return
    if args[0] == "--theme":
        theme = args[1]
        print(f"\n{theme} supply chain:")
        for r in theme_chain(conn, theme):
            tag = "" if r.listed else "  (private)"
            print(f"  L{r.layer}  {r.downstream:18} <- {r.upstream:8} {(r.upstream_name or '')[:22]:22} [{r.role}]{tag}")
    elif args[0] == "--upstream":
        tk = args[1]
        print(f"\n{tk} appears in these chains (what it feeds):")
        for r in feeds_of(conn, tk):
            print(f"  {r.theme:9} -> feeds {r.downstream:18} (L{r.layer}, {r.role})")
    else:
        target = args[0]
        print(f"\nSuppliers feeding {target} (the picks-and-shovels):")
        rows = suppliers_of(conn, target)
        if not rows:
            print("  (nothing mapped yet - add links to tbl_eb_supply_link)")
        for r in rows:
            tag = "" if r.listed else "  (private - not buyable)"
            note = f"  - {r.note}" if r.note else ""
            print(f"  L{r.layer}  {r.upstream:8} {(r.upstream_name or '')[:24]:24} [{r.role}]{tag}{note}")
    conn.close()


if __name__ == "__main__":
    main()
