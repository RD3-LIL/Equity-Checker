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

import argparse
import csv
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-magic", action="store_true",
                    help="Skip the Magic Formula price fetch, builds in seconds. Use this for 500 tickers.")
    args = ap.parse_args()
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
    # Latest auto score per ticker, mechanical proxy, separate from human evals
    auto = {}
    try:
        for row in conn.execute(
            "SELECT ticker, score_date, quality, growth, valuation, moat, risk, total, method "
            "FROM auto_scores ORDER BY ticker, score_date"
        ).fetchall():
            auto[row[0]] = row
    except Exception:
        auto = {}
    conn.close()

    # Sort best first: fewest RED flags, then highest Evaluator score,
    # then fewest YELLOW flags, then ticker. Only 7 tickers have an
    # Evaluator score, so RED count is the main ranking for the other 493.
    def sort_key(ticker):
        ev = evals.get(ticker)
        au = auto.get(ticker)
        total = ev[7] if ev else (au[7] if au else -1)
        reds = sum(1 for _fy, _n, flag, _d in checks.get(ticker, []) if flag == "RED")
        yellows = sum(1 for _fy, _n, flag, _d in checks.get(ticker, []) if flag == "YELLOW")
        return (reds, -total, yellows, ticker)
    tickers = sorted(fin.keys(), key=sort_key)
    today = date.today().isoformat()

    # Company names, if sp500_constituents.csv is next to this script.
    # Used for search by name, and shown as a tooltip, not on the tile
    # itself, so the tiles stay small.
    names = {}
    sectors = {}
    const_path = Path(__file__).with_name("sp500_constituents.csv")
    if const_path.exists():
        try:
            with const_path.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    sym = row.get("Symbol", "").strip().replace(".", "-")
                    names[sym] = row.get("Security", "").strip()
                    sectors[sym] = row.get("GICS Sector", "").strip() or "Other"
        except Exception:
            names = {}
            sectors = {}
    # AAPL, NVDA, RIVN are not in the S&P list in this run, add them by hand
    names.setdefault("AAPL", "Apple")
    names.setdefault("NVDA", "Nvidia")
    names.setdefault("RIVN", "Rivian")
    sectors.setdefault("AAPL", "Information Technology")
    sectors.setdefault("NVDA", "Information Technology")
    sectors.setdefault("RIVN", "Consumer Discretionary")

    # Magic Formula overlay: EY and ROC per ticker, ranked.
    # --no-magic skips the Yahoo price fetch entirely, builds in seconds.
    magic = {}
    magic_flag = None
    if HAS_MAGIC and not args.no_magic:
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
                try:
                    price = magic_mod.fetch_price(ticker)
                except Exception:
                    price = None
                ev = price * shares + debt_lt + debt_cur - excess if (shares and price is not None) else None
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
        if per_share is None:
            return "<p>DCF: N/A, missing shares outstanding.</p>"
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
            au = auto.get(ticker)
            if au:
                _t2, d, q, g, v, m, r, total, method = au
                ev_html = (f"<h3>Stock Evaluator, Auto score</h3><p><strong>{total}/100</strong> "
                           f"({d}, mechanical proxy, not a human evaluation)<br>Quality {q}, Growth {g}, Valuation {v}, Moat {m}, Risk {r}</p>")
            else:
                ev_html = "<h3>Stock Evaluator</h3><p>No evaluation saved yet.</p>"
        m = magic.get(ticker)
        if m and m.get("ey") is not None and m.get("roc") is not None and m.get("ev") is not None and m.get("price") is not None:
            flag = " <strong>MAGIC FLAG, best of this universe</strong>" if ticker == magic_flag else ""
            magic_html = (f"<h3>Magic Formula, overlay only, no points</h3>"
                          f"<p>Earnings Yield <strong>{m['ey']:.1%}</strong> (EBIT ${m['ebit']/1e9:.1f}B / EV ${m['ev']/1e9:.1f}B at ${m['price']:.2f}), "
                          f"Return on Capital <strong>{m['roc']:.1%}</strong> (EBIT / tangible ${m['nwc']/1e9:.1f}B NWC + ${m['ppe']/1e9:.1f}B PPE). "
                          f"EY rank {m.get('ey_rank', '-')}, ROC rank {m.get('roc_rank', '-')}, combined {m.get('combined', '-')}.{flag}</p>"
                          f"<p class='note'>AAPL's ROC is high because its working capital is negative and floored at 0, so tangible capital is just PPE. With only 3 tickers this is best of 3, not a top-decile market flag.</p>")
        elif m:
            magic_html = "<h3>Magic Formula</h3><p>Not available for this ticker, missing price or EV.</p>"
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

    # Two views, same tiny tiles, toggled by buttons.
    # View 1 (default): Best first, one grid, rank 1 top left.
    # View 2: By industry (GICS Sector), best first inside each group.
    def tile_html(ticker, rank=None):
        ev = evals.get(ticker)
        au = auto.get(ticker)
        total = ev[7] if ev else (au[7] if au else None)
        kind = "Human" if ev else ("Auto" if au else "-")
        reds = sum(1 for _fy, _n, flag, _d in checks.get(ticker, []) if flag == "RED")
        yellows = sum(1 for _fy, _n, flag, _d in checks.get(ticker, []) if flag == "YELLOW")
        cls = "clean" if reds == 0 else ("warn" if reds == 1 else "bad")
        score = f"{total}/100" if total is not None else "-"
        name = names.get(ticker, "")
        sector = sectors.get(ticker, "Other")
        rank_txt = f"#{rank} " if rank else ""
        mg = magic.get(ticker)
        magic_txt = ""
        if mg and mg.get("ey") is not None and mg.get("roc") is not None:
            magic_txt = f", EY {mg['ey']:.1%}, ROC {mg['roc']:.1%}, Magic combined {mg.get('combined', '-')}"
        return (f"<a class='tile {cls}' href='#{ticker}' data-ticker='{ticker}' data-name='{name.upper()}' data-sector='{sector.upper()}' title='{rank_txt}{ticker} {name}, {sector}, {reds} RED, {yellows} YELLOW, {kind} score {score}{magic_txt}'>"
                f"<span class='sym'>{ticker}</span></a>")

    best_tiles = "".join(tile_html(t, i) for i, t in enumerate(tickers, 1))
    # View 3: By Magic Formula, lowest combined rank first.
    # Combined = EY rank + ROC rank, so best Magic is #1.
    # Only tickers with a valid EY and ROC appear here.
    magic_valid = sorted(
        [t for t in magic if magic[t].get("combined") is not None],
        key=lambda t: (magic[t]["combined"], magic[t].get("ey_rank", 999), magic[t].get("roc_rank", 999), t),
    )
    if magic_valid:
        magic_tiles = "".join(tile_html(t, i) for i, t in enumerate(magic_valid, 1))
        magic_note = f"{len(magic_valid)} tickers with a Magic score, {len(tickers) - len(magic_valid)} missing price or EV."
    else:
        magic_tiles = ""
        magic_note = "No Magic scores in this build. Run python3 generate_report.py with no --no-magic to get prices and Magic ranks."
    by_sector = {}
    for ticker in tickers:
        by_sector.setdefault(sectors.get(ticker, "Other"), []).append(ticker)
    sector_blocks = ""
    for sector in sorted(by_sector):
        group = sorted(by_sector[sector], key=sort_key)
        tiles = "".join(tile_html(t) for t in group)
        sector_blocks += f"<div class='sector'><h3>{sector} ({len(group)})</h3><div class='grid'>{tiles}</div></div>"

    toc = f"""
    <section class="card" id="top"><h2>All tickers, best first</h2>
    <p class="note">Best = fewest RED flags, then highest score, then fewest YELLOW. Green = 0 RED, yellow = 1 RED, red = 2+ RED. Hover a square for its score, click it to dive into its full card below.</p>
    <p><button id="btn-best" onclick="showView('best')">Best first</button>
    <button id="btn-industry" onclick="showView('industry')">By industry</button>
    <button id="btn-magic" onclick="showView('magic')">By Magic</button></p>
    <input id="q" type="search" placeholder="Search ticker, company, or sector, e.g. NVDA, Nvidia, or Technology" autocomplete="off">
    <div id="view-best"><div class="grid">{best_tiles}</div></div>
    <div id="view-industry" style="display:none">{sector_blocks}</div>
    <div id="view-magic" style="display:none"><p class="note">{magic_note}</p><div class="grid">{magic_tiles}</div></div>
    <p class="note" id="count">{len(tickers)} tickers</p></section>
    <script>
    function showView(v) {{
      document.getElementById('view-best').style.display = v === 'best' ? '' : 'none';
      document.getElementById('view-industry').style.display = v === 'industry' ? '' : 'none';
      document.getElementById('view-magic').style.display = v === 'magic' ? '' : 'none';
    }}
    document.getElementById('q').addEventListener('input', function(e) {{
      var q = e.target.value.trim().toUpperCase();
      var shown = 0;
      document.querySelectorAll('#view-best .tile').forEach(function(t) {{
        var ok = !q || t.dataset.ticker.indexOf(q) !== -1 || (t.dataset.name || '').indexOf(q) !== -1 || (t.dataset.sector || '').indexOf(q) !== -1;
        t.style.display = ok ? '' : 'none';
        if (ok) shown++;
      }});
      document.querySelectorAll('#view-industry .tile').forEach(function(t) {{
        var ok = !q || t.dataset.ticker.indexOf(q) !== -1 || (t.dataset.name || '').indexOf(q) !== -1 || (t.dataset.sector || '').indexOf(q) !== -1;
        t.style.display = ok ? '' : 'none';
      }});
      document.querySelectorAll('#view-magic .tile').forEach(function(t) {{
        var ok = !q || t.dataset.ticker.indexOf(q) !== -1 || (t.dataset.name || '').indexOf(q) !== -1 || (t.dataset.sector || '').indexOf(q) !== -1;
        t.style.display = ok ? '' : 'none';
      }});
      document.querySelectorAll('#view-industry .sector').forEach(function(s) {{
        var any = false;
        s.querySelectorAll('.tile').forEach(function(t) {{ if (t.style.display !== 'none') any = true; }});
        s.style.display = any ? '' : 'none';
      }});
      document.getElementById('count').textContent = shown + ' tickers shown';
    }});
    </script>"""

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
body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; max-width: 1440px; margin: 0 auto; padding: 16px; color: #1f2328; background: #f6f8fa; }}
h1 {{ font-size: 28px; }} h2 {{ font-size: 22px; margin-top: 0; }} h3 {{ font-size: 16px; margin-bottom: 6px; }}
.card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 12px; padding: 20px; margin: 16px 0; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #d0d7de; vertical-align: top; }}
th {{ background: #f6f8fa; }}
.note {{ color: #57606a; font-size: 14px; }}
#q {{ width: 100%; padding: 10px 12px; font-size: 16px; border: 1px solid #d0d7de; border-radius: 8px; margin: 8px 0 12px; box-sizing: border-box; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(56px, 1fr)); gap: 4px; }}
.sector {{ margin: 12px 0; }}
.sector h3 {{ font-size: 14px; margin: 0 0 6px; color: #57606a; }}
.tile {{ display: flex; align-items: center; justify-content: center; padding: 8px 2px; border-radius: 6px; text-decoration: none; color: #1f2328; border: 1px solid #d0d7de; background: #fff; min-height: 38px; }}
.tile.clean {{ background: #dafbe1; border-color: #4ac26b; }}
.tile.warn {{ background: #fff8c5; border-color: #d4a72c; }}
.tile.bad {{ background: #ffebe9; border-color: #ff8182; }}
.tile .sym {{ font-weight: 700; font-size: 12px; }}
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
