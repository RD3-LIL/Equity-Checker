# Equity Bullshit Checker (prototype)

A real database + SQL + Python tool, not just a Muse web artifact.

It pulls free financial data for equities from the SEC EDGAR
Company Facts API, stores it in SQLite, and runs an institutional
equity-analysis checklist on top. Every flag is auditable: the
numbers behind it live in the database.

## What it is

- **Data layer (Python):** `equity_checker.py` parses EDGAR JSON
  in `data/` (AAPL, NVDA, RIVN, downloaded from
  `data.sec.gov/api/xbrl/companyfacts/`).
- **Database layer (SQLite):** `equity.db`, built from `schema.sql`.
  Tables: `companies`, `financials` (Raw), `checks` (Silver).
- **SQL layer:** `checks.sql`, the same checks as SQL queries.
  Cash conversion, accruals, dilution, revenue growth, leverage.
- **Checker layer (Python):** institutional checklist, GREEN /
  YELLOW / RED, with a plain-English detail per flag.

A web viewer can sit on top later. The database is the product.

## Ticklers

- AAPL: Apple Inc., CIK 320193
- NVDA: NVIDIA Corp, CIK 1045810
- RIVN: Rivian Automotive, the messy name, CIK 1874178
  (note: the first RIVN CIK tried, 1876049, was wrong and 404'd;
  the correct CIK was verified against SEC company_tickers.json)

## Results (real EDGAR data, run 2026-11-09)

AAPL, latest FY2026: revenue $478.8B, net income $132.2B,
OCF $145.1B. 0 RED, 1 YELLOW, 6 GREEN.
- Cash conversion 1.10x GREEN, accruals -3.2% GREEN,
  shares down 1.6% (buyback) GREEN, leverage 72% YELLOW.

NVDA, latest FY2026: revenue $215.9B, net income $120.1B,
OCF $102.7B. 0 RED, 2 YELLOW, 5 GREEN.
- Cash conversion 0.86x YELLOW, accruals 8.4% YELLOW,
  leverage 24% GREEN, shares down 0.7% GREEN.
- Note: receivables grew 66.8% while revenue grew 65.5%,
  gap only 1.3%, so that check stays GREEN. The YELLOWs are
  worth watching, not a red flag on their own.

RIVN, latest FY2025: revenue $5.4B, net income -$3.6B,
OCF -$0.8B. 5 RED, 1 YELLOW, 0 GREEN.
- Cash burn RED (OCF negative), profitability RED (loss in
  3 of last 3 years), dilution RED (shares up 9.6% YoY),
  receivables vs revenue RED (receivables up 25.3% vs
  revenue up 8.4%), accruals RED (-19.3% of assets).

That is the checker doing its job: clean names stay mostly
green, the messy name lights up.

## Checks

- cash_conversion: OCF / net income. GREEN >= 1.0, YELLOW >= 0.8.
- accruals_ratio: (net income - OCF) / total assets.
  RED if |x| > 10%, YELLOW if |x| > 5%.
- receivables_vs_revenue: receivables growth minus revenue growth.
  RED if gap > 10pp, YELLOW if > 5pp.
- dilution: shares outstanding YoY. RED > 5%, YELLOW > 2%.
- profitability: net loss in 2+ of last 3 years RED, 1 year YELLOW.
- leverage: total liabilities / total assets. RED > 80%, YELLOW > 60%.
- cash_burn: OCF negative RED, OCF < capex YELLOW.

## Known limits (honest)

- Stock splits break the raw dilution check. NVDA did a 10-for-1
  split in June 2024, so the SQL dilution query for FY2025 shows
  +893%. The Python checker only compares the latest two years
  (2026 vs 2025, both post-split), so it is not affected, but a
  production version needs split-adjusted shares.
- Accruals in a loss year are noisy. RIVN's -19.3% is real
  (earnings far from cash), but the direction is different from
  a profitable company inflating earnings.
- This is a prototype checklist, not a trained model. The next
  step is to tune thresholds on labeled examples (clean vs
  blowup companies) and add DCF / valuation sanity checks.

## DCF tool (lean, one file: dcf.py)

Separate from the checker, same database. It reads base FCF
(OCF - capex, latest fiscal year in equity.db), grows it for
5 years, discounts at WACC, adds a terminal value, subtracts
net debt, and divides by shares outstanding.

Defaults: growth 5%, WACC 9%, terminal growth 2.5%.
Assumptions are inputs, not facts. Growth: start with 3-5yr
historical growth, then temper. WACC: 8-10% is a normal sanity
range for a large cap. Terminal growth: 2-3%, long-run
inflation + GDP. The sensitivity table is the point, never
trust one number.

Results at defaults, run 2026-11-09, illustrative only:
- AAPL FY2026: base FCF $136.06B, fair value $161.49 per share.
  Sensitivity range $127.82 to $209.20. Terminal value is 75%
  of enterprise value, so the answer is mostly terminal value.
- NVDA FY2026: base FCF is an OCF proxy $102.72B (capex tag
  missing in EDGAR, warning printed, capex is small vs OCF),
  fair value $74.29 per share. Sensitivity $59.09 to $95.82.
- RIVN FY2025: FCF -$2.49B, DCF is N/A. Negative FCF has no
  DCF meaning, which is itself the signal (cash burn RED).

No current price is compared here, so this is not a buy/sell
call. Compare the fair value range to the market price yourself.

```
python3 dcf.py
python3 dcf.py AAPL --growth 0.07 --wacc 0.08
python3 test_dcf.py
```

## Evaluations table (Stock Evaluator, same database)

`evaluations` table in `schema.sql`, one row per ticker per date.
Framework frozen: Quality, Growth, Valuation, Moat, Risk, each
1-5. Total = round(sum / 25 * 100). Stance is the human call:
Own, Watch, or Avoid. A re-score is a new row, not an overwrite.
The bullshit checker is a veto, not a score: a RED flag does
not change the total, it blocks a Buy on its own.

Seeded with the 7 saved Evaluator evaluations, exact factor scores:
GOOGL 84 Own (5,4,4,5,3), AMZN 84 Own (4,5,4,5,3),
NXPI 80 Own (4,3,5,5,3), LMT 76 Own (4,2,4,5,4),
MU 68 Own (4,5,3,3,2), HOOD 60 Watch (4,4,2,3,2),
SPCX 68 Watch (4,5,1,5,2).

```
python3 evaluations.py seed
python3 evaluations.py list
python3 evaluations.py show SPCX
python3 evaluations.py add AAPL 5 4 4 5 3 --stance Own --date 2026-11-09 --notes "Strong moat, fair valuation"
sqlite3 equity.db "SELECT ticker, total, stance FROM evaluations ORDER BY total DESC;"
```

To see the SQL and Python parts side by side:
```
sqlite3 equity.db ".tables"
sqlite3 equity.db ".schema evaluations"
sqlite3 equity.db "SELECT * FROM checks WHERE flag = 'RED';"
```

## HTML report

`generate_report.py` reads equity.db and writes `index.html`,
one clean page with the checker flags, Evaluator scores, and
DCF fair values for AAPL, NVDA, and RIVN.

```
python3 generate_report.py
open index.html
```

## Push to GitHub

I cannot push to your GitHub from here, no account access.
On your Mac, in the equity-checker folder:

```
git init
git add .
git commit -m "Equity bullshit checker: EDGAR database, checks, DCF, Evaluator"
```

Then create a new repo on github.com (empty, no README),
copy its URL, and run, replacing the URL with yours:

```
git remote add origin https://github.com/YOURNAME/equity-checker.git
git branch -M main
git push -u origin main
```

The `.gitignore` keeps `equity.db` and the downloaded EDGAR
JSON out of git, the code and the HTML report go in.
To publish the report as a web page, turn on GitHub Pages
in the repo Settings, Pages, source main branch, and open
the URL it gives you for `index.html`.


## Run it

```
python3 equity_checker.py
python3 dcf.py
sqlite3 equity.db "SELECT * FROM checks;"
sqlite3 equity.db < checks.sql
```

Stdlib only, no dependencies. Python 3.10+.
Free data only, SEC EDGAR, no Bloomberg, no paid feed.
