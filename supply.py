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


# chokepoint = critical + few/sole suppliers + not substitutable (derived, never stored stale).
# hot chokepoint = chokepoint AND supply is constrained right now (sold out / backlogged).
_CHOKE = "(criticality IN ('essential','high') AND exclusivity IN ('sole','few') AND substitutable IS NOT TRUE)"


def crit_tag(r):
    """A short '!chokepoint' / 'CHOKEPOINT(sold out)' tag derived from the criticality fields."""
    crit = getattr(r, "criticality", None)
    if not crit:
        return ""
    choke = crit in ("essential", "high") and getattr(r, "exclusivity", "") in ("sole", "few") \
        and not getattr(r, "substitutable", False)
    if choke and getattr(r, "constrained_now", False):
        return f"  ** HOT CHOKEPOINT ({crit}/{r.exclusivity}, sold out): {r.constraint_note or ''}"
    if choke:
        return f"  * chokepoint ({crit}/{r.exclusivity})"
    return f"  [{crit}/{getattr(r,'exclusivity','?')}]"


def suppliers_of(conn, target, max_layer=3):
    """Names that feed `target` (a ticker or theme/leader label), nearest layer first."""
    cur = conn.cursor()
    dbex(cur, """SELECT upstream, upstream_name, layer, role, listed, note,
                        supply_type, criticality, exclusivity, competitors, substitutable,
                        constrained_now, constraint_note
                 FROM tbl_eb_supply_link
                 WHERE downstream = %s AND layer <= %s
                 ORDER BY layer, listed DESC, upstream""", target, max_layer)
    return cur.fetchall()


def chokepoints(conn, hot_only=False):
    """The structurally-protected suppliers: critical + few-suppliers + unsubstitutable.
    hot_only adds constrained_now (demand exceeds supply RIGHT NOW)."""
    cur = conn.cursor()
    extra = " AND constrained_now = true" if hot_only else ""
    dbex(cur, f"""SELECT DISTINCT ON (upstream) upstream, upstream_name, criticality, exclusivity,
                        competitors, constrained_now, constraint_note, theme
                 FROM tbl_eb_supply_link
                 WHERE {_CHOKE}{extra}
                 ORDER BY upstream, criticality""")
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
    elif args[0] in ("--chokepoints", "--hot"):
        hot = args[0] == "--hot"
        label = "HOT CHOKEPOINTS (critical + few-suppliers + unsubstitutable + SOLD OUT NOW)" if hot \
            else "CHOKEPOINTS (critical input, few/sole suppliers, no substitute)"
        print(f"\n{label}:")
        for r in chokepoints(conn, hot_only=hot):
            comp = f" vs {r.competitors}" if r.competitors else " (sole supplier)"
            cons = f"  - {r.constraint_note}" if r.constrained_now and r.constraint_note else ""
            print(f"  {r.upstream:8} {(r.upstream_name or '')[:22]:22} [{r.criticality}/{r.exclusivity}]{comp}{cons}")
    else:
        target = args[0]
        print(f"\nSuppliers feeding {target} (the picks-and-shovels):")
        rows = suppliers_of(conn, target)
        if not rows:
            print("  (nothing mapped yet - add links to tbl_eb_supply_link)")
        for r in rows:
            tag = "" if r.listed else "  (private - not buyable)"
            print(f"  L{r.layer}  {r.upstream:8} {(r.upstream_name or '')[:24]:24} [{r.role}]{tag}{crit_tag(r)}")
    conn.close()


if __name__ == "__main__":
    main()
