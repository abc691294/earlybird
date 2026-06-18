"""
t212_yf_map.py - port of the app's resolveInstrument.ts: map a T212 ticker to a
Yahoo Finance symbol. This is one of the two pieces we carry over from the old
work (the other being the raw instruments scrape). Everything else is built fresh.

resolve_yf({'ticker','currencyCode','workingScheduleId','shortName'}) -> 'IQE.L' etc.
"""
import re

COUNTRY_YF = {
    "US": "", "CA": ".TO", "BE": ".BR", "BB": ".BR", "AT": ".VI", "PT": ".LS",
    "GB": ".L", "IE": ".L", "DE": ".DE", "FR": ".PA", "NL": ".AS", "IT": ".MI",
    "ES": ".MC", "SE": ".ST", "DK": ".CO", "NO": ".OL", "CH": ".SW", "AU": ".AX", "HK": ".HK",
}
COUNTRY_LABEL = {
    "US": "", "CA": "TSX", "BE": "BRUSSELS", "BB": "BRUSSELS", "AT": "VIENNA", "PT": "LISBON",
    "GB": "LSE", "IE": "LSE", "DE": "XETRA", "FR": "PARIS", "NL": "AMSTERDAM", "IT": "MILAN",
    "ES": "MADRID", "SE": "STOCKHOLM", "DK": "COPENHAGEN", "NO": "OSLO", "CH": "SIX",
    "AU": "ASX", "HK": "HKEX",
}


def base_symbol(ticker: str) -> str:
    return re.sub(r"_[A-Z]{2,3}_EQ$|[a-z][0-9]?_EQ$|_EQ$", "", ticker)


def _country_code(ticker: str):
    m = re.search(r"_([A-Z]{2,3})_EQ$", ticker)
    return m.group(1) if m else None


def _exchange_letter(ticker: str):
    m = re.search(r"([a-z])[0-9]?_EQ$", ticker)
    return m.group(1) if m else None


def resolve_yf(inst: dict) -> str:
    ticker = inst["ticker"]
    currency = inst.get("currencyCode") or inst.get("currency")
    wsid = inst.get("workingScheduleId")
    base = inst.get("shortName") or base_symbol(ticker)

    cc = _country_code(ticker)
    if cc is not None:
        suffix = COUNTRY_YF.get(cc)
        return f"{base}{suffix}" if suffix is not None else base

    letter = _exchange_letter(ticker)
    if letter:
        if letter == "l": return f"{base}.L"
        if letter == "p": return f"{base}.PA"
        if letter == "e": return f"{base}.MC"
        if letter == "a": return f"{base}.AS"
        if letter == "m": return f"{base}.MI"
        if letter in ("o", "q"): return f"{base}.DU"
        if letter == "d":
            return f"{base}.DU" if wsid == 172 else f"{base}.DE"
        if letter == "s":
            if currency == "CHF": return f"{base}.SW"
            if currency in ("GBP", "GBX"): return f"{base}.L"
            return f"{base}.MC"

    if currency in ("GBP", "GBX"):
        return f"{base}.L"
    return base


def country_of(inst: dict):
    """Best-effort listing country for filtering to our markets."""
    cc = _country_code(inst["ticker"])
    if cc:
        return cc
    letter = _exchange_letter(inst["ticker"])
    by_letter = {"l": "GB", "p": "FR", "e": "ES", "a": "NL", "m": "IT",
                 "o": "DE", "q": "DE", "d": "DE", "s": None}
    if letter:
        if letter == "s":
            cur = inst.get("currencyCode") or inst.get("currency")
            return {"CHF": "CH", "GBP": "GB", "GBX": "GB"}.get(cur, "ES")
        return by_letter.get(letter)
    cur = inst.get("currencyCode") or inst.get("currency")
    return "GB" if cur in ("GBP", "GBX") else ("US" if cur == "USD" else None)


if __name__ == "__main__":
    import json, sys, collections
    path = sys.argv[1] if len(sys.argv) > 1 else \
        r"C:\Users\sbrow\OneDrive\Claude\projects\T212 Quant Pie\instruments_cache.json"
    data = json.load(open(path, encoding="utf-8"))
    stocks = [d for d in data if d.get("type") == "STOCK"]
    print(f"total {len(data)} | STOCK {len(stocks)}")

    suff = collections.Counter()
    ctry = collections.Counter()
    for d in stocks:
        y = resolve_yf(d)
        suff["." + y.split(".")[-1] if "." in y else "(US/none)"] += 1
        ctry[country_of(d) or "?"] += 1
    print("\nby suffix:", dict(suff.most_common()))
    print("by country:", dict(ctry.most_common(12)))

    print("\nspot-checks (resolved from name match):")
    want = ["IQE", "Lynas", "Riber", "AXT", "MP Materials", "Oxford Instrument",
            "NovaBay", "Novonix", "Aixtron", "Centrus", "Nuscale", "Rocket Lab"]
    for w in want:
        hit = next((d for d in stocks if w.lower() in (d.get("name") or "").lower()), None)
        if hit:
            print(f"  {w:18} {hit['ticker']:14} -> {resolve_yf(hit):14} ({hit.get('name')})")
        else:
            print(f"  {w:18} (not found in T212 universe)")
