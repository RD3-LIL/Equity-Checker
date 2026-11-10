-- Equity Checker database schema (SQLite).
-- Raw layer: financials straight from SEC EDGAR Company Facts, one row
-- per ticker per fiscal year, with source filing info.
-- Silver layer: checks, one row per ticker per check, with the
-- numbers that produced the flag, so every flag is auditable.

CREATE TABLE IF NOT EXISTS companies (
    ticker TEXT PRIMARY KEY,
    cik TEXT NOT NULL,
    name TEXT NOT NULL,
    benchmark TEXT
);

CREATE TABLE IF NOT EXISTS financials (
    ticker TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    fiscal_end TEXT,
    form TEXT,
    revenue REAL,
    net_income REAL,
    operating_cash_flow REAL,
    capex REAL,
    total_assets REAL,
    total_liabilities REAL,
    equity REAL,
    shares_outstanding REAL,
    receivables REAL,
    inventory REAL,
    source_url TEXT,
    PRIMARY KEY (ticker, fiscal_year),
    FOREIGN KEY (ticker) REFERENCES companies (ticker)
);

CREATE TABLE IF NOT EXISTS checks (
    ticker TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    check_name TEXT NOT NULL,
    flag TEXT NOT NULL CHECK (flag IN ('GREEN', 'YELLOW', 'RED')),
    value REAL,
    detail TEXT,
    PRIMARY KEY (ticker, fiscal_year, check_name),
    FOREIGN KEY (ticker, fiscal_year) REFERENCES financials (ticker, fiscal_year)
);

-- Evaluator layer: Stock Evaluator five-factor scores, saved in the
-- same database as the EDGAR financials and bullshit checks, so one
-- ticker has financials + flags + a human evaluation in one place.
-- Framework is frozen: Quality, Growth, Valuation, Moat, Risk,
-- each 1-5. Total = round(sum / 25 * 100). Do not add factors.
-- Stance is the human call, not computed: Own, Watch, or Avoid.
-- One row per ticker per evaluation date, so re-scores are history,
-- not overwrites. The bullshit checker is a veto, not a score:
-- a RED flag does not change the total, it blocks a Buy on its own.
CREATE TABLE IF NOT EXISTS evaluations (
    ticker TEXT NOT NULL,
    eval_date TEXT NOT NULL,
    quality INTEGER NOT NULL CHECK (quality BETWEEN 1 AND 5),
    growth INTEGER NOT NULL CHECK (growth BETWEEN 1 AND 5),
    valuation INTEGER NOT NULL CHECK (valuation BETWEEN 1 AND 5),
    moat INTEGER NOT NULL CHECK (moat BETWEEN 1 AND 5),
    risk INTEGER NOT NULL CHECK (risk BETWEEN 1 AND 5),
    total INTEGER NOT NULL,
    stance TEXT NOT NULL CHECK (stance IN ('Own', 'Watch', 'Avoid')),
    notes TEXT,
    PRIMARY KEY (ticker, eval_date)
);

-- SQL layer: the same checks, expressed as queries.
-- Example: cash conversion (OCF / net income) for the latest year.
-- SELECT ticker, fiscal_year,
--   operating_cash_flow / NULLIF(net_income, 0) AS cash_conversion
-- FROM financials WHERE net_income > 0;
--
-- Example: share dilution year over year.
-- SELECT a.ticker, a.fiscal_year,
--   (a.shares_outstanding - b.shares_outstanding)
--     / NULLIF(b.shares_outstanding, 0) AS dilution
-- FROM financials a JOIN financials b
--   ON a.ticker = b.ticker AND a.fiscal_year = b.fiscal_year + 1;
