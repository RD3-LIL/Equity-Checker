"""
S&P 500 loader for the Equity Bullshit Checker.

Adds S&P 500 tickers to the existing equity.db, it does NOT wipe
the DB like equity_checker.py build_db does. AAPL, NVDA, RIVN stay.

Source for the list: datasets/s-and-p-500-companies on GitHub,
constituents.csv, free, includes CIK per ticker. Dedupe by CIK,
dual-class tickers (GOOGL/GOOG, BRK.A/BRK.B) share one EDGAR filing,
keep the first ticker per CIK.

Usage:
    python3 sp500.py --limit 20      # test run, first 20 alphabetical
    python3 sp500.py --limit 20 --db equity.db
    python3 sp500.py --all            # full 500, see size warning below

Size warning, measured on the test run:
  Each EDGAR Company Facts JSON is roughly 1-5 MB.
  20 tickers is roughly 40-80 MB of data/*.json.
  500 tickers is roughly 1-2 GB, and will take 10-20 minutes at
  SEC rate limits, plus a large equity.db.
  Do NOT commit data/*.json or *.db to git, .gitignore already excludes them.
  For a full run, use --all --skip-existing and run it once, then
  reuse the DB, do not re-download every time.

Rate limit: SEC allows 10 requests per second. This script sleeps
0.5s between downloads, about 2 per second, well under the limit.
Failures are skipped and logged, they do not stop the run.
"""

import argparse
import csv
import sqlite3
import time
import urllib.request
from pathlib import Path

import equity_checker as ec

HERE = Path(__file__).parent
LIST_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
LIST_FILE = HERE / "sp500_constituents.csv"

def load_list():
    if not LIST_FILE.exists():
        print(f"Downloading S&P 500 list from {LIST_URL}...")
        req = urllib.request.Request(LIST_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            LIST_FILE.write_bytes(r.read())
    rows = list(csv.DictReader(open(LIST_FILE, newline="", encoding="utf-8")))
    # Dedupe by CIK, keep first ticker per CIK
    seen = {}
    for row in rows:
        cik = row["CIK"].strip()
        sym = row["Symbol"].strip().replace(".", "-")  # BRK.B -> BRK-B, EDGAR file name only
        if cik not in seen:
            seen[cik] = (sym, row["Security"].strip(), cik)
    return sorted(seen.values(), key=lambda x: x[0]), len(rows)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--db", default=str(HERE / "equity.db"))
    ap.add_argument("--skip-existing", action="store_true", default=True)
    args = ap.parse_args()

    companies, total_rows = load_list()
    print(f"S&P 500 list: {total_rows} tickers, {len(companies)} unique CIKs")
    if not args.all:
        companies = companies[: args.limit]
    print(f"Running for {len(companies)} tickers: {[c[0] for c in companies]}")

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB {db_path} does not exist, run python3 equity_checker.py first")
        return
    conn = sqlite3.connect(db_path)
    existing = {r[0] for r in conn.execute("SELECT ticker FROM companies").fetchall()}
    print(f"Already in DB: {len(existing)} tickers")

    ok, failed, skipped = [], [], []
    start = time.time()
    for ticker, name, cik in companies:
        if args.skip_existing and ticker in existing:
            skipped.append(ticker)
            continue
        # Patch the COMPANIES dict so extract_financials works for this ticker
        cik_padded = cik.zfill(10)
        ec.COMPANIES[ticker] = (cik_padded, name)
        try:
            rows = ec.extract_financials(ticker)
            if not rows:
                failed.append((ticker, "no financial rows"))
                continue
            conn.execute("INSERT OR REPLACE INTO companies VALUES (?, ?, ?, ?)", (ticker, cik_padded, name, None))
            for r in rows:
                conn.execute(
                    """INSERT OR REPLACE INTO financials
                       (ticker, fiscal_year, fiscal_end, form, revenue, net_income,
                        operating_cash_flow, capex, total_assets, total_liabilities,
                        equity, shares_outstanding, receivables, inventory, source_url)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ticker, r["fiscal_year"], r.get("fiscal_end"), r.get("form"),
                     r["revenue"], r["net_income"], r["operating_cash_flow"], r["capex"],
                     r["total_assets"], r["total_liabilities"], r["equity"],
                     r["shares_outstanding"], r["receivables"], r["inventory"],
                     f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json"),
                )
            conn.execute("DELETE FROM checks WHERE ticker = ?", (ticker,))
            checks = ec.run_checks(sorted(rows, key=lambda x: x["fiscal_year"]))
            for c in checks:
                conn.execute("INSERT INTO checks VALUES (?, ?, ?, ?, ?, ?)",
                             (ticker, c["fiscal_year"], c["check_name"], c["flag"], None, c["detail"]))
            conn.commit()
            reds = sum(1 for c in checks if c["flag"] == "RED")
            print(f"  OK {ticker}: FY{rows[-1]['fiscal_year']}, {reds} RED, {sum(1 for c in checks if c['flag']=='YELLOW')} YELLOW, {sum(1 for c in checks if c['flag']=='GREEN')} GREEN")
            ok.append(ticker)
        except Exception as e:
            failed.append((ticker, str(e)))
            print(f"  FAILED {ticker}: {e}")
        time.sleep(0.5)

    conn.close()
    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.0f}s: {len(ok)} added, {len(skipped)} skipped (already in DB), {len(failed)} failed")
    if failed:
        print("Failed:", failed)
    # Size report
    data_size = sum(p.stat().st_size for p in (HERE / "data").glob("*.json")) / 1e6 if (HERE / "data").exists() else 0
    print(f"data/*.json total now: {data_size:.0f} MB, DB: {db_path.stat().st_size/1e6:.1f} MB")
    if ok:
        print(f"Estimated full 500 at this rate: {elapsed/len(ok)*500/60:.0f} minutes, ~{data_size/max(len(ok),1)*500:.0f} MB of JSON")

if __name__ == "__main__":
    main()
