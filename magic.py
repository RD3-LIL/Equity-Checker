"""
Magic Formula overlay (Greenblatt), prototype, stdlib only.

Formula, per the research in Nov 2026:
  Earnings Yield = EBIT / Enterprise Value
  Return on Capital = EBIT / (Net Working Capital + Net Fixed Assets)
  Combined rank = EY rank + ROC rank, rank 1 = highest on each,
  lowest combined wins. No price target, no DCF, just a ranking.

Definitions used here, all from SEC EDGAR Company Facts:
  EBIT = OperatingIncomeLoss, latest fiscal year
  NWC = Current Assets - Current Liabilities, minus excess cash, floor at 0
  NFA = Property, Plant and Equipment, net
  Tangible capital = NWC + NFA
  Excess cash = max(cash - 3% x revenue, 0)
    Greenblatt never published his excess-cash definition.
    3% of sales is an implementer choice, flagged as such, not his.
  EV = market cap + interest-bearing debt - excess cash
  Market cap = current price x shares outstanding
  Price: Yahoo Finance chart endpoint, free, no key, with --price override.
    Price is the only non-EDGAR input, and it is shown in the output.

This is an OVERLAY FLAG, not extra points, per the lean v3 plan:
  top combined rank in the universe = Magic flag, it does not change
  the five-factor total. With only 3 tickers in this prototype the
  universe is tiny, so the flag means "best of these 3", not "top decile
  of the market". It only becomes a real top-decile flag with a large
  universe, like the S&P 500 loader.

Usage:
    python3 magic.py
    python3 magic.py AAPL --price 350
"""

import argparse
import json
import sqlite3
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "equity.db"
DATA = HERE / "data"

TAGS = {
    "ebit": ["OperatingIncomeLoss"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "ppe": ["PropertyPlantAndEquipmentNet"],
    "debt_lt": ["LongTermDebt"],
    "debt_current": ["DebtCurrent", "LongTermDebtCurrent"],
}

_JSON_CACHE = {}

def latest_tag(ticker, tag_list):
    path = DATA / f"{ticker}.json"
    if not path.exists():
        return None
    if ticker not in _JSON_CACHE:
        with open(path) as f:
            _JSON_CACHE[ticker] = json.load(f)
    data = _JSON_CACHE[ticker]
    facts = data.get("facts", {}).get("us-gaap", {})
    best = None
    for tag in tag_list:
        fact = facts.get(tag)
        if not fact:
            continue
        vals = [e for u in fact.get("units", {}).values() for e in u if e.get("form") == "10-K"]
        if not vals:
            continue
        vals.sort(key=lambda x: (x.get("fy", 0), x.get("filed", "")))
        cand = vals[-1]
        if best is None or cand.get("filed", "") >= best.get("filed", ""):
            best = cand
    return best["val"] if best else None

def fetch_price(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    return d["chart"]["result"][0]["meta"]["regularMarketPrice"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker", nargs="?", default=None)
    ap.add_argument("--price", type=float, default=None)
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT ticker, fiscal_year, revenue, shares_outstanding FROM financials ORDER BY ticker, fiscal_year"
    ).fetchall()
    latest = {}
    for ticker, fy, rev, shares in rows:
        latest[ticker] = (fy, rev, shares)
    conn.close()

    tickers = [args.ticker.upper()] if args.ticker else sorted(latest)
    results = []
    for ticker in tickers:
        fy, revenue, shares = latest[ticker]
        ebit = latest_tag(ticker, TAGS["ebit"])
        ca = latest_tag(ticker, TAGS["current_assets"])
        cl = latest_tag(ticker, TAGS["current_liabilities"])
        cash = latest_tag(ticker, TAGS["cash"]) or 0
        ppe = latest_tag(ticker, TAGS["ppe"])
        debt_lt = latest_tag(ticker, TAGS["debt_lt"]) or 0
        debt_cur = latest_tag(ticker, TAGS["debt_current"]) or 0
        if ebit is None or ca is None or cl is None or ppe is None:
            print(f"{ticker}: N/A, missing EBIT / working capital / PPE in EDGAR")
            continue
        excess = max(cash - 0.03 * (revenue or 0), 0)
        nwc = max(ca - cl - excess, 0)
        tangible = nwc + ppe
        roc = ebit / tangible if tangible else None
        price = args.price if args.price is not None else fetch_price(ticker)
        mktcap = price * shares if shares else None
        ev = mktcap + debt_lt + debt_cur - excess if mktcap is not None else None
        ey = ebit / ev if ev else None
        results.append({"ticker": ticker, "fy": fy, "ebit": ebit, "nwc": nwc, "ppe": ppe,
                        "tangible": tangible, "roc": roc, "price": price, "ev": ev, "ey": ey,
                        "excess_cash": excess})
        print(f"\n{ticker} FY{fy}")
        print(f"  EBIT ${ebit/1e9:.2f}B, NWC ${nwc/1e9:.2f}B, PPE ${ppe/1e9:.2f}B, tangible capital ${tangible/1e9:.2f}B")
        print(f"  Return on Capital: {roc:.1%}" if roc is not None else "  Return on Capital: n/a")
        print(f"  Price ${price:.2f}, market cap ${mktcap/1e9:.2f}B, EV ${ev/1e9:.2f}B, excess cash ${excess/1e9:.2f}B (3% of sales rule, implementer choice)")
        print(f"  Earnings Yield: {ey:.1%}" if ey is not None else "  Earnings Yield: n/a")

    # Rank, only if we have at least 2 tickers with both metrics
    valid = [r for r in results if r["roc"] is not None and r["ey"] is not None]
    if len(valid) >= 2:
        print("\nMagic rank (lowest combined wins, 1 = best on each):")
        ey_rank = {r["ticker"]: i+1 for i, r in enumerate(sorted(valid, key=lambda x: -x["ey"]))}
        roc_rank = {r["ticker"]: i+1 for i, r in enumerate(sorted(valid, key=lambda x: -x["roc"]))}
        ranked = sorted(valid, key=lambda r: ey_rank[r["ticker"]] + roc_rank[r["ticker"]])
        for i, r in enumerate(ranked):
            flag = "  <-- MAGIC FLAG, best of this universe" if i == 0 else ""
            print(f"  {i+1}. {r['ticker']}: EY rank {ey_rank[r['ticker']]}, ROC rank {roc_rank[r['ticker']]}, combined {ey_rank[r['ticker']] + roc_rank[r['ticker']]}{flag}")
        print("Note: with only 3 tickers this is 'best of 3', not a top-decile market flag. That needs a large universe.")

if __name__ == "__main__":
    main()
