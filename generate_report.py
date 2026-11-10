"""
Generate a clean HTML report from equity.db.

Reads the same database the checker, DCF, and Evaluator use,
and writes index.html, a single static page you can open in a
browser or publish with GitHub Pages. No server, no JavaScript
framework, just tables.

Sections:
  1. Bullshit checker, latest year per ticker, RED / YELLOW / GREEN
  2. Stock Evaluator scores, latest evaluation per ticker
  3. DCF fair value, defaults growth 5%, WACC 9%, terminal 2.5%
  4. Latest financials, revenue, net income, OCF, FCF

Usage:
    python3 generate_report.py
    open index.html
"""

import sqlite3
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "equity.db"
OUT = HERE / "index.html"

from dcf import dcf, net_debt

# Magic Formula overlay, best effort, needs data/*.json and a price feed.
# If it fails, the report still builds without the Magic section.
try:
    import magic as magic_mod
    HAS_MAGIC = True
except Exception:
    HAS_MAGIC = False

FLAG_COLOR = {"GREEN": "#1a7f37", "YELLOW": "#9a6700", "RED": "#cf222e"}

def b(v):
    return f"${v/1e9:.1f}B" if v is not None else "n/a"

def main():
    conn = sqlite3.connect(DB)
    # Latest financials per ticker
    fin = {}
    for row in conn.execute(
        "SELECT ticker, fiscal_year, revenue, net_income, operating_cash_flow, capex, shares_outstanding "
        "FROM financials ORDER BY ticker, fiscal_year"
    ).fetchall():
        fin[row[0]] = row
    # Latest checks per ticker (latest fiscal year in checks)
    checks = {}
    for ticker, fy, name, flag, detail in conn.execute(
        "SELECT ticker, fiscal_year, check_name, flag, detail FROM checks ORDER BY ticker, fiscal_year, check_name"
    ).fetchall():
        checks.setdefault(ticker, []).append((fy, name, flag, detail))
    # Latest evaluation per ticker
    evals = {}
    try:
        for row in conn.execute(
            "SELECT ticker, eval_date, quality, growth, valuation, moat, risk, total, stance, notes "
            "FROM evaluations ORDER BY ticker, eval_date"
        ).fetchall():
            evals[row[0]] = row
    except Exception:
        evals = {}
    conn.close()

    # Sort cards by Evaluator total, highest first, then by fewest RED flags.
    # Tickers with no evaluation go at the bottom, alphabetical.
    def sort_key(ticker):
        ev = evals.get(ticker)
        total = ev[7] if ev else -1
        reds = sum(1 for _fy, _n, flag, _d in checks.get(ticker, []) if flag == "RED")
        return (-total, reds, ticker)
    tickers = sorted(fin.keys(), key=sort_key)
    today = date.today().isoformat()

    # Magic Formula overlay: EY and ROC per ticker, ranked.
    magic = {}
    magic_flag = None
    if HAS_MAGIC:
        try:
            for ticker in tickers:
                fy, revenue, shares = fin[ticker][1], fin[ticker][2], fin[ticker][6]
                ebit = magic_mod.latest_tag(ticker, magic_mod.TAGS["ebit"])
                ca = magic_mod.latest_tag(ticker, magic_mod.TAGS["current_assets"])
                cl = magic_mod.latest_tag(ticker, magic_mod.TAGS["current_liabilities"])
                cash = magic_mod.latest_tag(ticker, magic_mod.TAGS["cash"]) or 0
                ppe = magic_mod.latest_tag(ticker, magic_mod.TAGS["ppe"])
                debt_lt = magic_mod.latest_tag(ticker, magic_mod.TAGS["debt_lt"]) or 0
                debt_cur = magic_mod.latest_tag(ticker, magic_mod.TAGS["debt_current"]) or 0
                if ebit is None or ca is None or cl is None or ppe is None:
                    continue
                excess = max(cash - 0.03 * (revenue or 0), 0)
                nwc = max(ca - cl - excess, 0)
                tangible = nwc + ppe
                roc = ebit / tangible if tangible else None
                price = magic_mod.fetch_price(ticker)
                ev = price * shares + debt_lt + debt_cur - excess if shares else None
                ey = ebit / ev if ev else None
                magic[ticker] = {"fy": fy, "ebit": ebit, "nwc": nwc, "ppe": ppe, "roc": roc, "price": price, "ev": ev, "ey": ey}
            valid = [t for t in magic if magic[t]["roc"] is not None and magic[t]["ey"] is not None]
            if len(valid) >= 2:
                ey_rank = {t: i+1 for i, t in enumerate(sorted(valid, key=lambda t: -magic[t]["ey"]))}
                roc_rank = {t: i+1 for i, t in enumerate(sorted(valid, key=lambda t: -magic[t]["roc"]))}
                for t in valid:
                    magic[t]["ey_rank"] = ey_rank[t]
                    magic[t]["roc_rank"] = roc_rank[t]
                    magic[t]["combined"] = ey_rank[t] + roc_rank[t]
                magic_flag = min(valid, key=lambda t: magic[t]["combined"])
        except Exception:
            magic = {}
            magic_flag = None

    def checker_rows(ticker):
        rows = checks.get(ticker, [])
        if not rows:
            return "<p>No checks yet.</p>"
        latest_fy = max(fy for fy, *_ in rows)
        rows = [r for r in rows if r[0] == latest_fy]
        out = [f"<p>Latest FY{latest_fy}</p><table><tr><th>Check</th><th>Flag</th><th>Detail</th></tr>"]
        for _fy, name, flag, detail in rows:
            c = FLAG_COLOR.get(flag, "#000")
            out.append(f"<tr><td>{name}</td><td style='color:{c};font-weight:700'>{flag}</td><td>{detail or ''}</td></tr>")
        out.append("</table>")
        return "".join(out)

    def dcf_block(ticker):
        _t, fy, _rev, _ni, ocf, capex, shares = fin[ticker]
        if ocf is None:
            return "<p>DCF: no cash flow data.</p>"
        fcf = ocf - capex if capex is not None else ocf
        if fcf <= 0:
            return f"<p><strong>DCF: N/A.</strong> FCF is negative (${fcf/1e9:.2f}B). A DCF on negative FCF has no meaning, this is itself the signal, see cash burn RED.</p>"
        nd, _cash, _lt = net_debt(ticker)
        result = dcf(fcf, 0.05, 0.09, 0.025, 5)
        ev, _pv, _pvt, _fcfs = result
        per_share = (ev - nd) / shares if shares else None
        # sensitivity range
        vals = []
        for g in (0.03, 0.05, 0.07):
            for w in (0.08, 0.09, 0.10):
                r = dcf(fcf, g, w, 0.025, 5)
                vals.append((r[0] - nd) / shares)
        lo, hi = min(vals), max(vals)
        return (f"<p>Base FCF ${fcf/1e9:.2f}B, FY{fy}. "
                f"Fair value <strong>${per_share:.2f}</strong> per share at growth 5%, WACC 9%, terminal 2.5%. "
                f"Sensitivity range ${lo:.2f} to ${hi:.2f}.</p>"
                f"<p>Net debt ${nd/1e9:.2f}B. Terminal value is about 75% of enterprise value, so the answer is mostly the terminal assumption.</p>")

    cards = []
    for ticker in tickers:
        _t, fy, rev, ni, ocf, capex, shares = fin[ticker]
        fcf = (ocf - capex) if (ocf is not None and capex is not None) else ocf
        reds = sum(1 for _fy, _n, flag, _d in checks.get(ticker, []) if flag == "RED")
        yellows = sum(1 for _fy, _n, flag, _d in checks.get(ticker, []) if flag == "YELLOW")
        ev = evals.get(ticker)
        ev_html = ""
        if ev:
            _t2, d, q, g, v, m, r, total, stance, notes = ev
            ev_html = (f"<h3>Stock Evaluator</h3><p><strong>{total}/100, {stance}</strong> "
                       f"({d})<br>Quality {q}, Growth {g}, Valuation {v}, Moat {m}, Risk {r}"
                       f"{'<br>' + notes if notes else ''}</p>")
        else:
            ev_html = "<h3>Stock Evaluator</h3><p>No evaluation saved yet.</p>"
        m = magic.get(ticker)
        if m:
            flag = " <strong>MAGIC FLAG, best of this universe</strong>" if ticker == magic_flag else ""
            magic_html = (f"<h3>Magic Formula, overlay only, no points</h3>"
                          f"<p>Earnings Yield <strong>{m['ey']:.1%}</strong> (EBIT ${m['ebit']/1e9:.1f}B / EV ${m['ev']/1e9:.1f}B at ${m['price']:.2f}), "
                          f"Return on Capital <strong>{m['roc']:.1%}</strong> (EBIT / tangible ${m['nwc']/1e9:.1f}B NWC + ${m['ppe']/1e9:.1f}B PPE). "
                          f"EY rank {m.get('ey_rank', '-')}, ROC rank {m.get('roc_rank', '-')}, combined {m.get('combined', '-')}.{flag}</p>"
                          f"<p class='note'>AAPL's ROC is high because its working capital is negative and floored at 0, so tangible capital is just PPE. With only 3 tickers this is best of 3, not a top-decile market flag.</p>")
        else:
            magic_html = "<h3>Magic Formula</h3><p>Not available, needs data/*.json and a price.</p>"
        cards.append(f"""
        <section class="card" id="{ticker}">
          <h2>{ticker}</h2>
          <p>FY{fy}: revenue {b(rev)}, net income {b(ni)}, OCF {b(ocf)}, FCF {b(fcf)}</p>
          <p><strong>{reds} RED, {yellows} YELLOW</strong> in the latest checks</p>
          <h3>Bullshit checker</h3>
          {checker_rows(ticker)}
          {ev_html}
          {magic_html}
          <h3>DCF</h3>
          {dcf_block(ticker)}
          <p><a href="#top">Back to top</a></p>
        </section>""")

    # Top table, sorted by score, highest first, each row links down to that ticker's card
    toc_rows = ""
    for ticker in tickers:
        ev = evals.get(ticker)
        total = ev[7] if ev else "-"
        stance = ev[8] if ev else "-"
        reds = sum(1 for _fy, _n, flag, _d in checks.get(ticker, []) if flag == "RED")
        toc_rows += f"<tr><td><a href='#{ticker}'>{ticker}</a></td><td>{total}</td><td>{stance}</td><td>{reds} RED</td></tr>"
    toc = f"""
    <section class="card" id="top"><h2>All tickers, sorted by score</h2>
    <p class="note">Highest Evaluator score first. Tickers with no evaluation yet are sorted by fewest RED flags. Click a ticker to dive into its full card below.</p>
    <table><tr><th>Ticker</th><th>Evaluator</th><th>Stance</th><th>Checker</th></tr>
    {toc_rows}
    </table></section>"""

    # Evaluator table, all saved evaluations, sorted by score, highest first
    eval_rows = ""
    for ticker in sorted(evals, key=lambda t: (-evals[t][7], t)):
        _t, d, q, g, v, m, r, total, stance, _n = evals[ticker]
        link = f"<a href='#{ticker}'>{ticker}</a>" if ticker in fin else ticker
        eval_rows += f"<tr><td>{link}</td><td>{d}</td><td>{q}</td><td>{g}</td><td>{v}</td><td>{m}</td><td>{r}</td><td><strong>{total}</strong></td><td>{stance}</td></tr>"
    eval_table = f"""
    <section class="card"><h2>Stock Evaluator, all saved</h2>
    <table><tr><th>Ticker</th><th>Date</th><th>Q</th><th>G</th><th>V</th><th>M</th><th>R</th><th>Total</th><th>Stance</th></tr>
    {eval_rows if eval_rows else '<tr><td colspan="9">No evaluations yet, run python3 evaluations.py seed</td></tr>'}
    </table></section>""" if evals else ""

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Equity Bullshit Checker, report {today}</title>
<style>
body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 24px; color: #1f2328; background: #f6f8fa; }}
h1 {{ font-size: 28px; }} h2 {{ font-size: 22px; margin-top: 0; }} h3 {{ font-size: 16px; margin-bottom: 6px; }}
.card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 12px; padding: 20px; margin: 16px 0; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #d0d7de; vertical-align: top; }}
th {{ background: #f6f8fa; }}
.note {{ color: #57606a; font-size: 14px; }}
</style></head><body>
<h1 id="top">Equity Bullshit Checker</h1>
<p class="note">Real SEC EDGAR data, SQLite database, SQL + Python checks, DCF, and Stock Evaluator scores, all in one place. Generated {today} from equity.db. This is not a buy or sell call, and no current price is compared here.</p>
{toc}
{''.join(cards)}
{eval_table}
<p class="note">Method: Possessions and efficiency are not used here, this is equities. Checks: cash conversion, accruals, receivables vs revenue, dilution, profitability, leverage, cash burn. DCF: 5 years FCF growth, WACC discount, terminal value, minus net debt, divided by shares. Evaluator: Quality, Growth, Valuation, Moat, Risk, each 1-5, total = sum / 25 x 100. Bullshit checker is a veto, a RED flag does not change a score, it blocks a Buy on its own.</p>
</body></html>"""
    OUT.write_text(html)
    print(f"Wrote {OUT} ({len(html)} bytes)")
    print(f"Tickers in report: {', '.join(tickers)}")

if __name__ == "__main__":
    main()
