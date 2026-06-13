"""seed_supply_chain.py - one-off: seed tbl_eb_supply_link with the chains mapped so far.
Rows: (theme, downstream, upstream, upstream_name, layer, role, listed, note).
downstream can be a ticker (NVDA) or a theme/leader label (AI compute, Quantum hardware)."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from eb_db import get_conn, dbex

LINKS = [
    # ---- AI compute stack: GPU -> memory -> optical -> substrate -> fab equipment ----
    ("AI", "AI compute", "NVDA", "Nvidia", 1, "GPUs", True, "The leader; everything below feeds it"),
    ("AI", "NVDA", "MU", "Micron", 1, "HBM memory", True, "High-bandwidth memory"),
    ("AI", "NVDA", "SNDK", "Sandisk", 1, "memory", True, ""),
    ("AI", "AI compute", "AVGO", "Broadcom", 1, "custom ASIC design", True, "Designs custom chips for GOOG/META/OpenAI"),
    ("AI", "AI compute", "MRVL", "Marvell", 1, "custom ASIC design", True, "Designs custom chips for AMZN/MSFT"),
    ("AI", "AVGO", "TSM", "TSMC", 2, "foundry", True, "Fabs the chips"),
    ("AI", "MRVL", "TSM", "TSMC", 2, "foundry", True, "Fabs the chips"),
    ("AI", "TSM", "LRCX", "Lam Research", 2, "etch/deposition tools", True, "Wafer fab equipment - fab cannot run without it"),
    ("AI", "TSM", "AMAT", "Applied Materials", 2, "deposition tools", True, "Wafer fab equipment"),
    ("AI", "TSM", "KLAC", "KLA Corp", 2, "process control/inspection", True, "Wafer fab equipment"),
    ("AI", "TSM", "ASML", "ASML", 2, "EUV lithography", True, "Single-source for advanced nodes"),
    ("AI", "AVGO", "SNPS", "Synopsys", 2, "EDA tools", True, "Chip design software"),
    ("AI", "AVGO", "CDNS", "Cadence", 2, "EDA tools", True, "Chip design software (duopoly with SNPS)"),
    # ---- AI optical interconnect: device -> substrate ----
    ("AI", "AI optical", "COHR", "Coherent", 1, "optical transceivers/InP devices", True, "Doubling InP capacity, Nvidia-backed"),
    ("AI", "AI optical", "LITE", "Lumentum", 1, "lasers/CPO", True, "Nvidia-backed laser supplier"),
    ("AI", "AI optical", "GLW", "Corning", 1, "optical fibre", True, "The glass nervous system, Nvidia-backed"),
    ("AI", "COHR", "IQE.L", "IQE plc", 2, "compound-semi epi wafers", True, "Epitaxy on InP/GaAs"),
    ("AI", "COHR", "AXTI", "AXT Inc", 2, "InP substrates", True, "Raw indium-phosphide wafers"),
    # ---- AI inference (the next leg) ----
    ("AI", "AI inference", "MRVL", "Marvell", 1, "inference silicon", True, "AMZN Trainium / MSFT Maia partner"),
    ("AI", "AI inference", "D-Matrix", "D-Matrix", 1, "inference chip", False, "Private; MSFT-backed via M12"),
    # ---- AI datacentre power/cooling/grid (money flowing here next) ----
    ("AI", "AI datacenter", "VRT", "Vertiv", 1, "cooling", True, "Extended"),
    ("AI", "AI datacenter", "ETN", "Eaton", 1, "power distribution", True, ""),
    ("AI", "AI datacenter", "GEV", "GE Vernova", 1, "gas turbines/power", True, ""),
    ("AI", "AI datacenter", "HUBB", "Hubbell", 2, "transformers/electrical", True, "3-yr backlogs - the real bottleneck"),
    ("AI", "AI datacenter", "CCJ", "Cameco", 3, "uranium fuel", True, "Nuclear restart picks-and-shovels"),
    ("AI", "AI datacenter", "FCX", "Freeport", 3, "copper", True, "~50t copper per datacenter; 3 demand vectors"),
    # ---- Quantum: hardware -> cryogenics -> lasers/control -> substrate ----
    ("Quantum", "Quantum hardware", "IONQ", "IonQ", 1, "trapped-ion computer", True, "Application layer (theme/binary)"),
    ("Quantum", "Quantum hardware", "QBTS", "D-Wave", 1, "annealing computer", True, "Optimisation/commercial-now angle"),
    ("Quantum", "Quantum hardware", "RGTI", "Rigetti", 1, "superconducting computer", True, "Milestone-driven"),
    ("Quantum", "Quantum hardware", "XNDU", "Xanadu Quantum", 1, "photonic computer", True, "Now public"),
    ("Quantum", "Quantum hardware", "Bluefors", "Bluefors", 1, "dilution refrigerators", False, "Private; the cryogenics leader - vendor-agnostic"),
    ("Quantum", "Quantum hardware", "FORM", "FormFactor", 1, "cryogenics + cryo test", True, "Vendor-agnostic; also AI-chip test"),
    ("Quantum", "Quantum hardware", "OXIG.L", "Oxford Instruments", 1, "cryogenics (dilution fridges)", True, "UK-listed; UK quantum push"),
    ("Quantum", "Quantum hardware", "KEYS", "Keysight", 1, "control/test electronics", True, ""),
    ("Quantum", "IONQ", "COHR", "Coherent", 2, "lasers (trapped-ion)", True, ""),
    ("Quantum", "Quantum hardware", "IQE.L", "IQE plc", 2, "compound-semi substrates", True, "Also feeds AI optical (dual exposure)"),
    ("Quantum", "Quobly", "STM", "STMicroelectronics", 2, "silicon-spin foundry", True, "Fabs Quobly's qubits (private co)"),
    ("Quantum", "post-quantum security", "LAES", "SEALSQ", 1, "post-quantum chips", True, "Defensive side; dilution-funded - spec"),
    # ---- Space: orbital compute -> satellites -> launch ----
    ("Space", "orbital data", "NVDA", "Nvidia", 1, "orbital AI compute (Space-1)", True, "GTC 2026 - the pump driving the theme"),
    ("Space", "orbital data", "PL", "Planet Labs", 1, "earth-observation sats", True, "Named by Nvidia"),
    ("Space", "orbital data", "SATL", "Satellogic", 1, "earth-observation sats", True, "HELD"),
    ("Space", "orbital data", "ICEYE", "ICEYE", 1, "SAR satellites", False, "Private; $12B valuation"),
    ("Space", "space launch", "RKLB", "Rocket Lab", 1, "launch vehicles", True, "Quality leader, fully priced"),
    ("Space", "space launch", "FLY", "Firefly Aerospace", 1, "launch + lunar", True, "Small, off-highs - the early one"),
    ("Space", "satellite-to-phone", "ASTS", "AST SpaceMobile", 1, "direct-to-phone sats", True, "At scale, diluting"),
    # ---- Defence / drones ----
    ("Defence", "defence drones", "RCAT", "Red Cat", 1, "drone OEM (Army SRR)", True, ""),
    ("Defence", "RCAT", "UMAC", "Unusual Machines", 1, "NDAA drone components", True, "Sells motors to RCAT + Army"),
    ("Defence", "defence drones", "LASE", "Laser Photonics", 1, "counter-drone laser", True, "Speculative"),
]


def main():
    conn = get_conn(); cur = conn.cursor()
    ins = 0
    for theme, down, up, upname, layer, role, listed, note in LINKS:
        dbex(cur, """INSERT INTO tbl_eb_supply_link
            (theme,downstream,upstream,upstream_name,layer,role,listed,note,source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'seed')
            ON CONFLICT (theme,downstream,upstream,role) DO NOTHING""",
            theme, down, up, upname, layer, role, listed, note)
        ins += 1
    conn.commit()
    dbex(cur, "SELECT COUNT(*) n, COUNT(DISTINCT theme) t FROM tbl_eb_supply_link")
    r = cur.fetchone()
    print(f"seeded; table now has {r.n} links across {r.t} themes")
    conn.close()


if __name__ == "__main__":
    main()
