"""
Lean DCF tool (prototype, one file, stdlib only).

Reads free cash flow from the equity-checker SQLite database
(equity.db, built from SEC EDGAR Company Facts), then values
the business as the present value of 5 years of FCF plus a
terminal value. No three-statement monster.

  FCF = operating cash flow - capex (latest fiscal year in DB)
  FCF_t = FCF * (1 + growth) ** t, t = 1..5
  Terminal value = FCF_6 / (WACC - terminal_growth)
  Enterprise value = PV(FCF_1..5) + PV(terminal value)
  Equity value = EV - net debt
  Fair value per share = equity value / shares outstanding

Net debt is read from the EDGAR JSON in data/ when available:
long-term debt + current debt - cash. If a tag is missing,
net debt defaults to 0 and a warning is printed.

Assumptions are inputs, not facts. Defaults are deliberately
plain: growth 5%, WACC 9%, terminal growth 2.5%.
How to choose them (see README):
  growth: start with 3-5yr historical FCF/revenue growth, then temper.
  WACC: 8-10% for a large cap is a normal sanity range.
  terminal growth: 2-3%, roughly long-run inflation + GDP.
Never trust one number: the sensitivity table is the point.

Usage:
    python3 dcf.py                  # all tickers, defaults
    python3 dcf.py AAPL --growth 0.07 --wacc 0.08
    python3 dcf.py NVDA --growth 0.10 --wacc 0.09 --terminal 0.03
"""

import argparse
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "equity.db"
DATA_DIR = HERE / "data"


_JSON_CACHE = {}

def latest_tag(ticker, tag):
    """Latest 10-K value for an EDGAR tag, or None."""
    path = DATA_DIR / f"{ticker}.json"
    if not path.exists():
        return None
    if ticker not in _JSON_CACHE:
        with open(path) as f:
            _JSON_CACHE[ticker] = json.load(f)
    data = _JSON_CACHE[ticker]
    fact = data.get("facts", {}).get("us-gaap", {}).get(tag)
    if not fact:
        return None
    vals = [e for u in fact.get("units", {}).values() for e in u if e.get("form") == "10-K"]
    if not vals:
        return None
    vals.sort(key=lambda x: (x.get("fy", 0), x.get("filed", "")))
    return vals[-1]["val"]


def net_debt(ticker):
    cash = latest_tag(ticker, "CashAndCashEquivalentsAtCarryingValue") or 0
    lt = latest_tag(ticker, "LongTermDebt") or 0
    # LongTermDebt in EDGAR is often total debt for these filers;
    # use it as the debt proxy and say so.
    nd = lt - cash
    return nd, cash, lt


def dcf(fcf, growth=0.05, wacc=0.09, terminal=0.025, years=5):
    """Return (enterprise_value, pv_fcf, pv_terminal, yearly_fcfs)."""
    if fcf is None or fcf <= 0:
        return None
    fcfs = [fcf * (1 + growth) ** t for t in range(1, years + 1)]
    pv_fcf = sum(cf / (1 + wacc) ** t for t, cf in enumerate(fcfs, start=1))
    fcf_next = fcfs[-1] * (1 + terminal)
    tv = fcf_next / (wacc - terminal)
    pv_tv = tv / (1 + wacc) ** years
    return pv_fcf + pv_tv, pv_fcf, pv_tv, fcfs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker", nargs="?", default=None)
    ap.add_argument("--growth", type=float, default=0.05)
    ap.add_argument("--wacc", type=float, default=0.09)
    ap.add_argument("--terminal", type=float, default=0.025)
    ap.add_argument("--years", type=int, default=5)
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            "SELECT ticker, fiscal_year, operating_cash_flow, capex, shares_outstanding "
            "FROM financials ORDER BY ticker, fiscal_year"
        ).fetchall()
    except Exception:
        print("No financials table in equity.db yet.")
        print("Run this first, it rebuilds the database from EDGAR:")
        print("  python3 equity_checker.py")
        print("Then re-seed the evaluations (it is safe to run again):")
        print("  python3 evaluations.py seed")
        return
    latest = {}
    for ticker, fy, ocf, capex, shares in rows:
        latest[ticker] = (fy, ocf, capex, shares)
    tickers = [args.ticker] if args.ticker else sorted(latest)
    for ticker in tickers:
        fy, ocf, capex, shares = latest[ticker]
        print(f"\n{ticker}  FY{fy}")
        if ocf is None:
            print("  N/A: no operating cash flow in DB")
            continue
        if capex is None:
            print(f"  Warning: capex missing in EDGAR tag, using OCF as FCF (overstates by capex, NVDA capex is small vs OCF)")
            fcf = ocf
        else:
            fcf = ocf - capex
        print(f"  Base FCF: ${fcf/1e9:.2f}B  (OCF ${ocf/1e9:.2f}B - capex ${capex/1e9:.2f}B)" if capex is not None else f"  Base FCF (OCF proxy): ${fcf/1e9:.2f}B")
        if fcf <= 0:
            print("  N/A: FCF is negative, a DCF on negative FCF has no meaning. This is itself the signal, see the bullshit checker (RIVN: cash burn RED).")
            continue
        nd, cash, lt_debt = net_debt(ticker)
        print(f"  Net debt: ${nd/1e9:.2f}B  (LT debt ${lt_debt/1e9:.2f}B - cash ${cash/1e9:.2f}B)")
        result = dcf(fcf, args.growth, args.wacc, args.terminal, args.years)
        ev, pv_fcf, pv_tv, fcfs = result
        equity = ev - nd
        per_share = equity / shares if shares else None
        print(f"  Assumptions: growth {args.growth:.1%}, WACC {args.wacc:.1%}, terminal {args.terminal:.1%}, {args.years} years")
        print(f"  PV of FCF years 1-{args.years}: ${pv_fcf/1e9:.2f}B, PV of terminal value: ${pv_tv/1e9:.2f}B")
        print(f"  Enterprise value: ${ev/1e9:.2f}B, equity value: ${equity/1e9:.2f}B")
        print(f"  Fair value per share: ${per_share:.2f}" if per_share else "  Fair value per share: n/a (no shares in DB)")
        print(f"  Terminal value is {pv_tv/ev:.0%} of enterprise value, if that is over ~70%, the answer is mostly terminal value, not the forecast.")
        print("  Sensitivity (fair value per share, growth rows x WACC cols):")
        print("           WACC 8.0%   9.0%   10.0%")
        for g in (0.03, 0.05, 0.07):
            vals = []
            for w in (0.08, 0.09, 0.10):
                r = dcf(fcf, g, w, args.terminal, args.years)
                v = (r[0] - nd) / shares if r and shares else float("nan")
                vals.append(f"${v:7.2f}")
            print(f"  growth {g:.0%}  " + "  ".join(vals))
    conn.close()


if __name__ == "__main__":
    main()
