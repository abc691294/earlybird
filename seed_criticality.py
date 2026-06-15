"""seed_criticality.py - backfill criticality on the key supply links. Focus on the
chokepoints and the constrained-now names (the timing edge). Keyed by upstream ticker +
role so it updates the existing rows. Idempotent."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from eb_db import get_conn, dbex

# upstream, role-fragment, supply_type, criticality, exclusivity, competitors, substitutable, constrained_now, constraint_note
C = [
    # ===== THE MONOPOLIES / CHOKEPOINTS =====
    ("ASML",  "litho",     "equipment", "essential", "sole", "",                          False, True,  "EUV machines backlogged; only maker on earth"),
    ("TSM",   "foundry",   "service",   "essential", "few",  "Samsung, Intel (trailing)", False, True,  "Advanced-node capacity tight"),
    ("LRCX",  "etch",      "equipment", "essential", "few",  "AMAT, TEL",                 False, False, ""),
    ("AMAT",  "deposition","equipment", "essential", "few",  "LRCX, TEL",                 False, False, ""),
    ("KLAC",  "process control","equipment","high",  "sole", "",                          False, False, "Dominant in inspection/metrology"),
    ("SNPS",  "EDA",       "ip",        "essential", "few",  "CDNS",                      False, False, "Cannot design a chip without it (duopoly)"),
    ("CDNS",  "EDA",       "ip",        "essential", "few",  "SNPS",                      False, False, "EDA duopoly"),
    # ===== MEMORY PIPELINE - THE 'SOLD OUT' SIGNAL =====
    ("MU",    "HBM",       "component", "essential", "few",  "SK Hynix, Samsung",         False, True,  "HBM sold out through 2026/2027 - demand exceeds supply"),
    ("SNDK",  "memory",    "component", "high",      "few",  "MU, Samsung, Kioxia",       False, True,  "NAND tightening on AI demand"),
    # ===== OPTICAL / PHOTONICS - Nvidia-backed, capacity ramping =====
    ("COHR",  "InP",       "component", "essential", "few",  "LITE, IQE",                 False, True,  "InP capacity doubling but demand ahead; EML lasers 40-60% undersupplied to 2027"),
    ("LITE",  "laser",     "component", "high",      "few",  "COHR",                      False, True,  "CPO laser orders exceed capacity"),
    ("GLW",   "fibre",     "component", "high",      "few",  "Prysmian, Sumitomo",        False, True,  "Optical fibre tight on AI datacentre buildout"),
    ("AXTI",  "InP substrate","raw material","essential","few","IQE, Sumitomo",           False, True,  "InP substrate near capacity limit"),
    ("IQE.L", "epi wafer", "raw material","high",    "few",  "AXTI",                      False, False, ""),
    # ===== POWER / GRID - the 3-year backlog names =====
    ("HUBB",  "transformer","component","essential", "few",  "Hitachi, GE Vernova, Eaton",False, True,  "Transformers: ~3-year backlogs"),
    ("HTHIY", "transformer","component","essential", "few",  "Hubbell, Siemens Energy",   False, True,  "Grid transformers multi-year lead times"),
    ("ETN",   "power dist","component", "high",      "few",  "ABB, Schneider, Vertiv",    False, False, ""),
    ("VRT",   "cooling",   "equipment", "high",      "few",  "Schneider, nVent, Modine",  False, True,  "Datacentre cooling demand outpacing supply"),
    ("GEV",   "turbine",   "equipment", "essential", "few",  "Siemens Energy, Mitsubishi",False, True,  "Gas turbine slots sold out years ahead"),
    # ===== NUCLEAR FUEL =====
    ("CCJ",   "uranium",   "raw material","essential","few", "Kazatomprom, Orano",        False, True,  "Uranium supply deficit; restart demand"),
    # ===== RARE EARTHS / COPPER =====
    ("MP",    "rare earth","raw material","essential","few", "Lynas, MP, China (excluded)",False,True,  "ex-China rare-earth/magnet supply scarce"),
    ("FCX",   "copper",    "raw material","essential","many","SCCO, BHP, Antofagasta",    False, True,  "Copper: 3 demand vectors, no new big mines for years"),
    # ===== QUANTUM CRYOGENICS (vendor-agnostic chokepoint) =====
    ("FORM",  "cryo",      "equipment", "high",      "few",  "Bluefors(pvt), Oxford Inst",False, True,  "Dilution fridges 6-12mo lead, $1-5M each"),
    ("OXIG.L","cryo",      "equipment", "high",      "few",  "Bluefors(pvt), FormFactor", False, True,  "Cryogenics capacity tight"),
    # ===== CYBER (software - not capacity-constrained, but mission-critical) =====
    ("PANW",  "platform",  "service",   "high",      "few",  "FTNT, CRWD, ZS",            True,  False, "Substitutable between vendors"),
    ("CRWD",  "endpoint",  "service",   "high",      "few",  "S, MSFT, PANW",             True,  False, "Substitutable"),
    # ===== EDA already above; semis test =====
    ("TER",   "test",      "equipment", "high",      "few",  "Advantest",                 False, False, "Chip test duopoly with Advantest"),
]


def main():
    conn = get_conn(); cur = conn.cursor()
    n = 0
    for up, frag, stype, crit, excl, comp, subst, cons, note in C:
        dbex(cur, """UPDATE tbl_eb_supply_link
            SET supply_type=%s, criticality=%s, exclusivity=%s, competitors=%s,
                substitutable=%s, constrained_now=%s, constraint_note=%s
            WHERE upstream=%s AND role ILIKE %s""",
            stype, crit, excl, comp, subst, cons, note, up, f"%{frag}%")
        n += cur.rowcount
    conn.commit()
    print(f"criticality set on {n} links")
    # show the hot chokepoints (essential/high + sole/few + not substitutable + constrained now)
    dbex(cur, """SELECT upstream, upstream_name, role, criticality, exclusivity, constraint_note
        FROM tbl_eb_supply_link
        WHERE criticality IN ('essential','high') AND exclusivity IN ('sole','few')
          AND substitutable = false AND constrained_now = true
        ORDER BY criticality, upstream""")
    print("\nHOT CHOKEPOINTS (critical + few-suppliers + unsubstitutable + sold-out-now):")
    for r in cur.fetchall():
        print(f"  {r.upstream:8} {(r.upstream_name or '')[:20]:20} [{r.criticality}/{r.exclusivity}] {r.constraint_note}")
    conn.close()


if __name__ == "__main__":
    main()
