#!/usr/bin/env python3
"""Auto-score all tickers in equity.db, mechanical proxy only.

This is NOT the human Stock Evaluator. The 7 human evaluations in the
evaluations table are untouched. Auto scores live in a separate table,
auto_scores, and are labelled "Auto" in the report, so the two are never
confused.

Rules, locked 2026-11-09, all from data already in equity.db, plus a
Yahoo price for Valuation only (same fetch as magic.py):
- Quality: cash conversion OCF / net income, latest year, plus ROA.
  OCF/NI >= 1.0 and ROA >= 0.10 -> 5, >= 0.8 / >= 0.05 -> 4,
  >= 0.6 / >= 0.02 -> 3, positive NI -> 2, loss or OCF < 0 -> 1.
  Simplified to a points sum, see code.
- Growth: latest revenue YoY. > 20% -> 5, > 12% -> 4, > 6% -> 3,
  > 0% -> 2, <= 0% -> 1. If only one year, 3.
- Valuation: Earnings Yield = EBIT / EV, ranked by percentile across
  the whole universe scored in this run. Top quintile -> 5, then 4, 3, 2, 1.
  EBIT and EV come from magic.py (EDGAR JSON + Yahoo price). If price or
  EBIT is missing, Valuation = 3, neutral, and the ticker is flagged.
  Use --no-price to skip the price fetch entirely, then all Valuation = 3.
- Moat: net margin (NI / revenue) plus ROA. Margin >= 25% and ROA >= 10%
  -> 5, >= 15% / >= 7% -> 4, >= 8% / >= 4% -> 3, >= 0% -> 2, loss -> 1.
- Risk: leverage (liabilities / assets) plus cash burn plus RED flags.
  Leverage <= 0.40 and 0 RED -> 5, <= 0.60 and <= 1 RED -> 4,
  <= 0.75 -> 3, <= 0.85 -> 2, else 1. OCF negative forces at most 2.
  Higher Risk score means safer, same as the human Evaluator.

Total = round(sum / 25 * 100), same formula as the human Evaluator.
Run: python3 auto_score.py [--db equity.db] [--no-price] [--limit N]
Then: python3 generate_report.py --no-magic
"""
import argparse
import sqlite3
from datetime import date
from pathlib import Path

DB = Path(__file__).with_name("equity.db")

try:
    import magic as magic_mod
    HAS_MAGIC = True
except Exception:
    magic_mod = None
    HAS_MAGIC = False


def clamp(x, lo=1, hi=5):
    return max(lo, min(hi, int(x)))


def score_quality(ocf, ni, assets):
    if ni is None or ocf is None:
        return 3
    if ni <= 0 or ocf < 0:
        return 1
    conv = ocf / ni if ni else 0
    roa = ni / assets if assets else 0
    pts = 0
    pts += 2 if conv >= 1.0 else (1 if conv >= 0.8 else 0)
    pts += 2 if roa >= 0.10 else (1 if roa >= 0.05 else 0)
    pts += 1 if conv >= 0.8 and roa >= 0.02 else 0
    return clamp(pts)


def score_growth(rows):
    # v2: 3-year revenue CAGR if 4 years are available, not just last YoY.
    # A flat last year on a steady grower (LMT, NXPI) should not score 1.
    # rows: [(fy, revenue, ni), ...] sorted by fy ascending
    if len(rows) < 2:
        return 3
    revs = [r[1] for r in rows if r[1]]
    if len(revs) < 2:
        return 3
    if len(revs) >= 4 and revs[0] > 0 and revs[-1] > 0:
        g = (revs[-1] / revs[0]) ** (1 / (len(revs) - 1)) - 1
    else:
        g = (revs[-1] - revs[-2]) / abs(revs[-2])
    if g > 0.20:
        return 5
    if g > 0.12:
        return 4
    if g > 0.06:
        return 3
    if g > 0:
        return 2
    return 1


def score_moat(ni, rev, assets, rows=None):
    # v2: average margin and ROA over all years in the DB, plus a point for
    # being profitable every year. A steady compounder with a modest margin
    # (LMT, NXPI) should not score 2 just because last year margin was low.
    if rows:
        if all(r[3] is not None and r[3] <= 0 for r in rows):
            return 1
        margins = [r[3] / r[2] for r in rows if r[2] and r[3] is not None]
        roas = [r[3] / r[6] for r in rows if r[6] and r[3] is not None]
        if not margins:
            return 3
        margin = sum(margins) / len(margins)
        roa = sum(roas) / len(roas) if roas else 0
        if all(r[3] is not None and r[3] > 0 for r in rows):
            roa += 0.01
    else:
        if ni is None or rev is None or not rev:
            return 3
        if ni <= 0:
            return 1
        margin = ni / rev
        roa = ni / assets if assets else 0
    if ni is not None and ni <= 0 and not rows:
        return 1
    if margin >= 0.25 and roa >= 0.10:
        return 5
    if margin >= 0.15 and roa >= 0.07:
        return 4
    if margin >= 0.08 and roa >= 0.04:
        return 3
    if margin >= 0.03:
        return 3
    return 2


def score_risk(liab, assets, ocf, reds):
    if liab is None or assets is None or not assets:
        return 3
    lev = liab / assets
    if ocf is not None and ocf < 0:
        return 2 if reds <= 1 else 1
    if lev <= 0.40 and reds == 0:
        return 5
    if lev <= 0.60 and reds <= 1:
        return 4
    if lev <= 0.75:
        return 3
    if lev <= 0.85:
        return 2
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--no-price", action="store_true",
                    help="Skip Yahoo price fetch, Valuation = 3 for all.")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("""CREATE TABLE IF NOT EXISTS auto_scores (
        ticker TEXT, score_date TEXT, quality INTEGER, growth INTEGER,
        valuation INTEGER, moat INTEGER, risk INTEGER, total INTEGER,
        method TEXT, PRIMARY KEY (ticker, score_date))""")
    fin = {}
    for row in conn.execute(
        "SELECT ticker, fiscal_year, revenue, net_income, operating_cash_flow, "
        "capex, total_assets, total_liabilities, shares_outstanding "
        "FROM financials ORDER BY ticker, fiscal_year"
    ).fetchall():
        fin.setdefault(row[0], []).append(row)
    reds = {}
    for ticker, flag in conn.execute(
        "SELECT ticker, flag FROM checks WHERE fiscal_year = "
        "(SELECT MAX(fiscal_year) FROM checks c2 WHERE c2.ticker = checks.ticker)"
    ).fetchall():
        if flag == "RED":
            reds[ticker] = reds.get(ticker, 0) + 1
    human = {}
    try:
        for t, total in conn.execute("SELECT ticker, total FROM evaluations"):
            human[t] = total
    except Exception:
        human = {}

    tickers = sorted(fin.keys())
    if args.limit:
        tickers = tickers[:args.limit]

    # Valuation: Earnings Yield per ticker, then percentile rank.
    eys = {}
    if HAS_MAGIC and not args.no_price:
        for ticker in tickers:
            try:
                rows = fin[ticker]
                latest = rows[-1]
                _t, fy, revenue, _ni, _ocf, _capex, _assets, _liab, shares = latest
                ebit = magic_mod.latest_tag(ticker, magic_mod.TAGS["ebit"])
                ca = magic_mod.latest_tag(ticker, magic_mod.TAGS["current_assets"])
                cl = magic_mod.latest_tag(ticker, magic_mod.TAGS["current_liabilities"])
                cash = magic_mod.latest_tag(ticker, magic_mod.TAGS["cash"]) or 0
                debt_lt = magic_mod.latest_tag(ticker, magic_mod.TAGS["debt_lt"]) or 0
                debt_cur = magic_mod.latest_tag(ticker, magic_mod.TAGS["debt_current"]) or 0
                if ebit is None or shares is None:
                    continue
                excess = max(cash - 0.03 * (revenue or 0), 0)
                price = magic_mod.fetch_price(ticker)
                if price is None:
                    continue
                ev = price * shares + debt_lt + debt_cur - excess
                if ev:
                    eys[ticker] = ebit / ev
            except Exception:
                continue
    # Quintile thresholds, top 20% -> 5, next 20% -> 4, etc.
    sorted_ey = sorted(eys.values())
    def ey_score(ey):
        if ey is None or not sorted_ey:
            return 3
        n = len(sorted_ey)
        rank = sum(1 for v in sorted_ey if v <= ey) / n
        if rank >= 0.8:
            return 5
        if rank >= 0.6:
            return 4
        if rank >= 0.4:
            return 3
        if rank >= 0.2:
            return 2
        return 1

    today = date.today().isoformat()
    scored = []
    for ticker in tickers:
        rows = fin[ticker]
        _t, fy, rev, ni, ocf, capex, assets, liab, shares = rows[-1]
        q = score_quality(ocf, ni, assets)
        g = score_growth([(r[1], r[2], r[3]) for r in rows])
        v = ey_score(eys.get(ticker))
        m = score_moat(ni, rev, assets, rows=rows)
        r = score_risk(liab, assets, ocf, reds.get(ticker, 0))
        total = round((q + g + v + m + r) / 25 * 100)
        conn.execute(
            "INSERT OR REPLACE INTO auto_scores VALUES (?,?,?,?,?,?,?,?,?)",
            (ticker, today, q, g, v, m, r, total, "auto-v2, EDGAR + Yahoo EY"),
        )
        scored.append((ticker, q, g, v, m, r, total))
    conn.commit()

    # Comparison vs human scores, where both exist.
    both = [(t, human[t], dict((s[0], s[6]) for s in scored).get(t))
            for t in human if t in dict((s[0], s[6]) for s in scored)]
    scored.sort(key=lambda s: -s[6])
    print(f"Auto-scored {len(scored)} tickers, {today}")
    print("Top 10:")
    for t, q, g, v, m, r, total in scored[:10]:
        print(f"  {t}: {total}  Q{q} G{g} V{v} M{m} R{r}")
    print("Bottom 5:")
    for t, q, g, v, m, r, total in scored[-5:]:
        print(f"  {t}: {total}  Q{q} G{g} V{v} M{m} R{r}")
    if both:
        print("Human vs Auto, tickers with both:")
        for t, h, a in sorted(both):
            print(f"  {t}: human {h}, auto {a}, diff {a - h:+d}")
    else:
        print("No ticker has both a human and an auto score in this DB, "
              "so no comparison yet. Seed evaluations first, or run on the full DB.")
    conn.close()


if __name__ == "__main__":
    main()
