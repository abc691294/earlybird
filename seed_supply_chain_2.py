"""seed_supply_chain_2.py - extend the supply-chain map to the themes that had ZERO links.
Builds the who-feeds-whom chains for Semiconductors, Power/grid, Cybersecurity, Nuclear/power,
Energy storage, Robotics, Rare earths, AI infrastructure. Only the relationships that MATTER
(the picks-and-shovels layer the method cares about), not every name. Idempotent."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from eb_db import get_conn, dbex

# (theme, downstream, upstream, upstream_name, layer, role, listed, note)
LINKS = [
    # ===== SEMICONDUCTORS: chip makers <- equipment <- materials =====
    ("Semiconductors", "chip foundry", "TSM", "TSMC", 1, "leading-edge foundry", True, "Fabs the world's advanced chips"),
    ("Semiconductors", "chip foundry", "INTC", "Intel", 1, "foundry + IDM", True, ""),
    ("Semiconductors", "TSM", "ASML", "ASML", 2, "EUV lithography", True, "Single-source for advanced nodes"),
    ("Semiconductors", "TSM", "LRCX", "Lam Research", 2, "etch/deposition", True, "Wafer fab equipment"),
    ("Semiconductors", "TSM", "AMAT", "Applied Materials", 2, "deposition", True, "Wafer fab equipment"),
    ("Semiconductors", "TSM", "KLAC", "KLA", 2, "process control/inspection", True, ""),
    ("Semiconductors", "TSM", "TER", "Teradyne", 2, "test equipment", True, "Back-end chip test"),
    ("Semiconductors", "ASML", "IPGP", "IPG Photonics", 3, "lasers for litho", True, ""),
    ("Semiconductors", "chip design", "SNPS", "Synopsys", 2, "EDA tools", True, "Cannot design a chip without it"),
    ("Semiconductors", "chip design", "CDNS", "Cadence", 2, "EDA tools", True, ""),
    ("Semiconductors", "chip foundry", "ENTG", "Entegris", 3, "specialty materials/filtration", True, "Process chemicals & materials"),
    # ===== AI INFRASTRUCTURE: datacentre compute <- servers <- memory/networking =====
    ("AI infrastructure", "AI datacenter", "AVGO", "Broadcom", 1, "custom ASIC + networking", True, ""),
    ("AI infrastructure", "AI datacenter", "ANET", "Arista", 1, "datacenter networking", True, "AI cluster switches"),
    ("AI infrastructure", "AI datacenter", "DELL", "Dell", 1, "AI servers", True, ""),
    ("AI infrastructure", "AI datacenter", "SMCI", "Super Micro", 1, "AI servers", True, ""),
    ("AI infrastructure", "AI datacenter", "MU", "Micron", 1, "HBM memory", True, ""),
    ("AI infrastructure", "AI datacenter", "STX", "Seagate", 1, "storage", True, ""),
    ("AI infrastructure", "AI datacenter", "WDC", "Western Digital", 1, "storage", True, ""),
    ("AI infrastructure", "AI datacenter", "EQIX", "Equinix", 1, "datacenter REIT", True, "Physical datacentre capacity"),
    # ===== POWER / GRID: the AI power bottleneck =====
    ("Power/grid", "AI datacenter power", "ETN", "Eaton", 1, "power distribution/management", True, ""),
    ("Power/grid", "AI datacenter power", "VRT", "Vertiv", 1, "power + cooling", True, ""),
    ("Power/grid", "AI datacenter power", "ABBNY", "ABB", 1, "switchgear/electrification", True, ""),
    ("Power/grid", "AI datacenter power", "SBGSY", "Schneider Electric", 1, "power management", True, ""),
    ("Power/grid", "AI datacenter power", "GEV", "GE Vernova", 1, "gas turbines/grid", True, "Generation + grid kit"),
    ("Power/grid", "grid buildout", "HUBB", "Hubbell", 2, "transformers/electrical", True, "3-yr backlogs - real bottleneck"),
    ("Power/grid", "grid buildout", "PWR", "Quanta Services", 2, "grid construction", True, ""),
    ("Power/grid", "grid buildout", "PRYMY", "Prysmian", 2, "power cabling", True, ""),
    ("Power/grid", "grid buildout", "HTHIY", "Hitachi Energy", 2, "transformers/HVDC", True, ""),
    # ===== CYBERSECURITY: platforms <- nothing upstream (software), but ecosystem peers =====
    ("Cybersecurity", "cyber platform", "PANW", "Palo Alto", 1, "network/cloud security platform", True, "Leader"),
    ("Cybersecurity", "cyber platform", "CRWD", "CrowdStrike", 1, "endpoint/XDR platform", True, ""),
    ("Cybersecurity", "cyber platform", "ZS", "Zscaler", 1, "zero-trust/SASE", True, ""),
    ("Cybersecurity", "cyber platform", "FTNT", "Fortinet", 1, "firewall/network security", True, ""),
    ("Cybersecurity", "cyber platform", "S", "SentinelOne", 1, "AI endpoint security", True, "Smaller/earlier"),
    ("Cybersecurity", "cyber platform", "OKTA", "Okta", 1, "identity security", True, ""),
    ("Cybersecurity", "cyber platform", "NET", "Cloudflare", 1, "edge security/network", True, ""),
    # ===== NUCLEAR / POWER: reactors <- fuel <- components =====
    ("Nuclear/power", "nuclear", "CCJ", "Cameco", 1, "uranium fuel", True, "Picks-and-shovels of the nuclear restart"),
    ("Nuclear/power", "nuclear", "BWXT", "BWX Technologies", 1, "naval/SMR reactor components", True, ""),
    ("Nuclear/power", "nuclear", "OKLO", "Oklo", 1, "small modular reactor", True, "Pre-revenue SMR"),
    ("Nuclear/power", "nuclear", "SMR", "NuScale", 1, "small modular reactor", True, ""),
    ("Nuclear/power", "nuclear", "CEG", "Constellation", 1, "nuclear generation", True, "Existing fleet powering AI"),
    ("Nuclear/power", "CCJ", "DNN", "Denison Mines", 2, "uranium mining", True, ""),
    ("Nuclear/power", "CCJ", "NXE", "NexGen Energy", 2, "uranium mining", True, ""),
    # ===== ENERGY STORAGE: batteries <- materials =====
    ("Energy storage", "battery storage", "VST", "Vistra", 1, "grid-scale storage operator", True, ""),
    ("Energy storage", "battery", "SQM", "SQM", 2, "lithium", True, "Battery material"),
    ("Energy storage", "battery", "ALB", "Albemarle", 2, "lithium", True, ""),
    ("Energy storage", "battery", "ENVX", "Enovix", 1, "advanced battery cells", True, "Early/spec"),
    ("Energy storage", "battery", "QS", "QuantumScape", 1, "solid-state battery", True, "Pre-revenue"),
    # ===== ROBOTICS / AUTONOMY: robots <- sensors/compute/actuation =====
    ("Robotics/autonomy", "robotics", "NVDA", "Nvidia", 1, "robot AI compute (Jetson/GR00T)", True, "Physical AI leader"),
    ("Robotics/autonomy", "robotics", "OUST", "Ouster", 1, "lidar/sensing", True, ""),
    ("Robotics/autonomy", "robotics", "TER", "Teradyne", 1, "industrial robots (Universal Robots)", True, ""),
    ("Robotics/autonomy", "robotics", "ISRG", "Intuitive Surgical", 1, "surgical robotics", True, ""),
    ("Robotics/autonomy", "robotics", "PATH", "UiPath", 1, "software automation", True, ""),
    # ===== RARE EARTHS / MATERIALS: magnets/materials for everything above =====
    ("Rare earths/materials", "rare earths", "MP", "MP Materials", 1, "rare-earth mining/magnets", True, "US rare-earth supply"),
    ("Rare earths/materials", "rare earths", "LYSDY", "Lynas", 1, "rare-earth mining", True, "ex-China supply"),
    ("Rare earths/materials", "rare earths", "USAR", "USA Rare Earth", 1, "rare-earth magnets", True, ""),
    ("Rare earths/materials", "rare earths", "FCX", "Freeport", 1, "copper", True, "Electrification metal - 3 demand vectors"),
    ("Rare earths/materials", "rare earths", "SCCO", "Southern Copper", 1, "copper", True, ""),
]


def main():
    conn = get_conn(); cur = conn.cursor()
    ins = 0
    for L in LINKS:
        dbex(cur, """INSERT INTO tbl_eb_supply_link
            (theme,downstream,upstream,upstream_name,layer,role,listed,note,source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'seed2')
            ON CONFLICT (theme,downstream,upstream,role) DO NOTHING""", *L)
        ins += 1
    conn.commit()
    dbex(cur, "SELECT theme, COUNT(*) n FROM tbl_eb_supply_link GROUP BY theme ORDER BY n DESC")
    print("supply map now:")
    for r in cur.fetchall():
        print(f"  {r.theme:24} {r.n}")
    conn.close()


if __name__ == "__main__":
    main()
