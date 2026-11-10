"""
Equity Bullshit Checker (prototype).

Real database, not a Muse artifact. Layers:

  1. Data layer (Python): pull SEC EDGAR Company Facts JSON
     (free, official), parse annual 10-K values.
  2. Database layer (SQLite): schema.sql, tables companies,
     financials (Raw), checks (Silver, auditable).
  3. SQL layer: checks.sql, the same checks as queries.
  4. Python checker layer: institutional equity checks,
     GREEN / YELLOW / RED with the numbers behind each flag.
  5. Output: printed report + checks table in the DB.

Tickers in this prototype: AAPL, NVDA, RIVN (the messy name).
RIVN CIK is 1874178, verified against SEC company_tickers.json.

Checks (institutional checklist, simplified for a prototype):
  - cash_conversion: OCF / net income. GREEN >= 1.0, YELLOW >= 0.8, else RED.
  - accruals_ratio: (net income - OCF) / total assets. RED if |x| > 0.10,
    YELLOW if |x| > 0.05.
  - receivables_vs_revenue: receivables growth minus revenue growth.
    RED if > 0.10, YELLOW if > 0.05.
  - dilution: shares outstanding YoY change. RED if > 5%, YELLOW if > 2%.
  - profitability: net income negative in 2+ of last 3 years => RED,
    1 year => YELLOW.
  - leverage: total liabilities / total assets. RED > 0.80, YELLOW > 0.60.
  - cash_burn: OCF negative => RED. OCF < capex => YELLOW.

Usage:
    python equity_checker.py            # build DB from data/*.json, run checks
    python equity_checker.py --db equity.db
"""

import argparse
import json
import sqlite3
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"

COMPANIES = {
    "AAPL": ("0000320193", "Apple Inc."),
    "NVDA": ("0001045810", "NVIDIA Corp"),
    "RIVN": ("0001874178", "Rivian Automotive, Inc."),
}

# EDGAR US-GAAP tags. Tag names change over time and by filer,
# so we try several and pick the latest-filed value per fiscal year.
TAGS = {
    # Tag drift: newer filings often use ProfitLoss instead of NetIncomeLoss,
    # and RevenueFromContractWithCustomerIncludingAssessedTax instead of Revenues.
    # Keep all candidates, pick_annual keeps the latest-filed value per year.
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "Revenues"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", "NetCashProvidedByOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity"],
    "shares_outstanding": ["CommonStockSharesOutstanding"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
}


def pick_annual(fact):
    """Pick annual 10-K values, one per fiscal year, latest filing wins.

    EDGAR Company Facts lists every unit and every filing. We keep
    only USD (or shares), form 10-K, fiscal year, and the most
    recently filed value for that year.
    """
    by_year = {}
    for unit_name, entries in fact.get("units", {}).items():
        for e in entries:
            if e.get("form") != "10-K":
                continue
            fy = e.get("fy")
            if not fy:
                continue
            # Keep the latest filed entry for this fiscal year.
            prev = by_year.get(fy)
            if prev is None or e.get("filed", "") >= prev.get("filed", ""):
                by_year[fy] = e
    return by_year


def download_if_missing(ticker, cik):
    """Fetch EDGAR Company Facts JSON if data/<TICKER>.json is missing.
    SEC requires a descriptive User-Agent; this is free, no key."""
    path = DATA_DIR / f"{ticker}.json"
    if path.exists():
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    print(f"  Downloading {ticker} from EDGAR...")
    req = urllib.request.Request(url, headers={"User-Agent": "equity-checker prototype (learning project)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        path.write_bytes(r.read())


def extract_financials(ticker):
    cik, _name = COMPANIES[ticker]
    download_if_missing(ticker, cik)
    path = DATA_DIR / f"{ticker}.json"
    with open(path) as f:
        data = json.load(f)
    facts = data.get("facts", {}).get("us-gaap", {})
    # Collect per-field, per-year values. For each year, keep the
    # latest-filed entry across all candidate tags.
    per_field = {}
    for field, tag_list in TAGS.items():
        per_field[field] = {}
        for tag in tag_list:
            fact = facts.get(tag)
            if not fact:
                continue
            for fy, e in pick_annual(fact).items():
                prev = per_field[field].get(fy)
                if prev is None or e.get("filed", "") >= prev.get("filed", ""):
                    per_field[field][fy] = e
    # Only keep years with revenue and net income, last 4 complete years.
    complete_years = sorted(
        {fy for fy in per_field["revenue"] if fy in per_field["net_income"]},
        reverse=True,
    )[:4]
    years = complete_years
    rows = []
    for fy in sorted(years):
        row = {"ticker": ticker, "fiscal_year": fy}
        for field in TAGS:
            e = per_field[field].get(fy)
            row[field] = e["val"] if e else None
            if field == "revenue" and e:
                row["fiscal_end"] = e.get("end")
                row["form"] = e.get("form")
        rows.append(row)
    return rows


def flag_cash_conversion(ni, ocf):
    if ni is None or ocf is None or ni <= 0:
        return None, "N/A - loss year or missing"
    v = ocf / ni
    if v >= 1.0:
        return "GREEN", f"OCF {ocf/1e9:.1f}B covers net income {ni/1e9:.1f}B ({v:.2f}x)"
    if v >= 0.8:
        return "YELLOW", f"OCF covers {v:.2f}x of net income"
    return "RED", f"OCF covers only {v:.2f}x of net income, earnings not backed by cash"


def flag_accruals(ni, ocf, assets):
    if ni is None or ocf is None or not assets:
        return None, "missing"
    v = (ni - ocf) / assets
    if abs(v) > 0.10:
        return "RED", f"Accruals {v:.1%} of assets, earnings far from cash"
    if abs(v) > 0.05:
        return "YELLOW", f"Accruals {v:.1%} of assets"
    return "GREEN", f"Accruals {v:.1%} of assets"


def flag_dilution(prev_shares, shares):
    if prev_shares is None or shares is None or not prev_shares:
        return None, "missing"
    v = (shares - prev_shares) / prev_shares
    if v > 0.05:
        return "RED", f"Shares outstanding up {v:.1%} year over year"
    if v > 0.02:
        return "YELLOW", f"Shares outstanding up {v:.1%} year over year"
    if v < 0:
        return "GREEN", f"Shares outstanding down {abs(v):.1%} (buyback)"
    return "GREEN", f"Shares outstanding up only {v:.1%}"


def flag_leverage(liab, assets):
    if liab is None or not assets:
        return None, "missing"
    v = liab / assets
    if v > 0.80:
        return "RED", f"Liabilities {v:.0%} of assets"
    if v > 0.60:
        return "YELLOW", f"Liabilities {v:.0%} of assets"
    return "GREEN", f"Liabilities {v:.0%} of assets"


def run_checks(rows):
    """rows: sorted by fiscal_year ascending. Returns list of check dicts."""
    checks = []
    # Latest year checks.
    latest = rows[-1]
    fy = latest["fiscal_year"]
    for name, fn, args in [
        ("cash_conversion", flag_cash_conversion, (latest["net_income"], latest["operating_cash_flow"])),
        ("accruals_ratio", flag_accruals, (latest["net_income"], latest["operating_cash_flow"], latest["total_assets"])),
        ("leverage", flag_leverage, (latest["total_liabilities"], latest["total_assets"])),
    ]:
        flag, detail = fn(*args)
        if flag:
            checks.append({"fiscal_year": fy, "check_name": name, "flag": flag, "detail": detail})
    # Year-over-year checks, latest year vs prior.
    if len(rows) >= 2:
        prev, cur = rows[-2], rows[-1]
        flag, detail = flag_dilution(prev["shares_outstanding"], cur["shares_outstanding"])
        if flag:
            checks.append({"fiscal_year": fy, "check_name": "dilution", "flag": flag, "detail": detail})
        # Receivables vs revenue growth.
        if all(v is not None for v in (prev["receivables"], cur["receivables"], prev["revenue"], cur["revenue"])) and prev["receivables"] and prev["revenue"]:
            rec_g = (cur["receivables"] - prev["receivables"]) / prev["receivables"]
            rev_g = (cur["revenue"] - prev["revenue"]) / prev["revenue"]
            gap = rec_g - rev_g
            if gap > 0.10:
                f = "RED"
            elif gap > 0.05:
                f = "YELLOW"
            else:
                f = "GREEN"
            checks.append({"fiscal_year": fy, "check_name": "receivables_vs_revenue", "flag": f,
                           "detail": f"Receivables up {rec_g:.1%}, revenue up {rev_g:.1%}, gap {gap:.1%}"})
        # Cash burn.
        if cur["operating_cash_flow"] is not None:
            if cur["operating_cash_flow"] < 0:
                checks.append({"fiscal_year": fy, "check_name": "cash_burn", "flag": "RED",
                               "detail": f"Operating cash flow negative ({cur['operating_cash_flow']/1e9:.2f}B)"})
            elif cur["capex"] is not None and cur["operating_cash_flow"] < cur["capex"]:
                checks.append({"fiscal_year": fy, "check_name": "cash_burn", "flag": "YELLOW",
                               "detail": "Operating cash flow does not cover capex"})
            else:
                checks.append({"fiscal_year": fy, "check_name": "cash_burn", "flag": "GREEN",
                               "detail": "Operating cash flow covers capex"})
    # Profitability over last 3 years.
    last3 = rows[-3:]
    losses = sum(1 for r in last3 if r["net_income"] is not None and r["net_income"] < 0)
    if losses >= 2:
        checks.append({"fiscal_year": fy, "check_name": "profitability", "flag": "RED",
                       "detail": f"Net loss in {losses} of last {len(last3)} years"})
    elif losses == 1:
        checks.append({"fiscal_year": fy, "check_name": "profitability", "flag": "YELLOW",
                       "detail": "Net loss in 1 of last 3 years"})
    else:
        checks.append({"fiscal_year": fy, "check_name": "profitability", "flag": "GREEN",
                       "detail": "Profitable in all of last 3 years"})
    return checks


def build_db(db_path):
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    schema = (HERE / "schema.sql").read_text()
    # schema.sql includes comments; executescript handles it.
    conn.executescript(schema)
    all_checks = {}
    for ticker, (cik, name) in COMPANIES.items():
        conn.execute("INSERT INTO companies VALUES (?, ?, ?, ?)", (ticker, cik, name, None))
        rows = extract_financials(ticker)
        for r in rows:
            conn.execute(
                """INSERT INTO financials
                   (ticker, fiscal_year, fiscal_end, form, revenue, net_income,
                    operating_cash_flow, capex, total_assets, total_liabilities,
                    equity, shares_outstanding, receivables, inventory, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ticker, r["fiscal_year"], r.get("fiscal_end"), r.get("form"),
                 r["revenue"], r["net_income"], r["operating_cash_flow"], r["capex"],
                 r["total_assets"], r["total_liabilities"], r["equity"],
                 r["shares_outstanding"], r["receivables"], r["inventory"],
                 f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"),
            )
        checks = run_checks(sorted(rows, key=lambda x: x["fiscal_year"]))
        all_checks[ticker] = (rows, checks)
        for c in checks:
            conn.execute(
                "INSERT INTO checks VALUES (?, ?, ?, ?, ?, ?)",
                (ticker, c["fiscal_year"], c["check_name"], c["flag"], None, c["detail"]),
            )
    conn.commit()
    return conn, all_checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(HERE / "equity.db"))
    args = ap.parse_args()
    conn, all_checks = build_db(args.db)
    for ticker, (rows, checks) in all_checks.items():
        print(f"\n{ticker} - {COMPANIES[ticker][1]}")
        print("  Fiscal years in DB:", [r["fiscal_year"] for r in rows])
        latest = rows[-1]
        def b(v):
            return f"${v/1e9:.1f}B" if v is not None else "n/a"
        print(f"  Latest FY{latest['fiscal_year']}: revenue {b(latest['revenue'])}, "
              f"net income {b(latest['net_income'])}, "
              f"OCF {b(latest['operating_cash_flow'])}")
        reds = sum(1 for c in checks if c["flag"] == "RED")
        print(f"  Flags: {reds} RED, {sum(1 for c in checks if c['flag']=='YELLOW')} YELLOW, "
              f"{sum(1 for c in checks if c['flag']=='GREEN')} GREEN")
        for c in checks:
            print(f"    {c['flag']:6} {c['check_name']:24} {c['detail']}")
    print(f"\nDB written to {args.db}")
    cur = conn.execute("SELECT COUNT(*) FROM financials")
    print("financials rows:", cur.fetchone()[0])
    cur = conn.execute("SELECT COUNT(*) FROM checks")
    print("checks rows:", cur.fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
