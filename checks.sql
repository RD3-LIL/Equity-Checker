-- SQL layer for the Equity Checker.
-- Run against equity.db after build_db.py has loaded financials.
-- All values are decimals; returns are in the filing currency (USD).

-- 1. Cash conversion: operating cash flow / net income
SELECT ticker, fiscal_year,
  operating_cash_flow / NULLIF(net_income, 0) AS cash_conversion,
  CASE
    WHEN net_income <= 0 THEN 'N/A - loss year'
    WHEN operating_cash_flow / net_income >= 1.0 THEN 'GREEN'
    WHEN operating_cash_flow / net_income >= 0.8 THEN 'YELLOW'
    ELSE 'RED'
  END AS flag
FROM financials
ORDER BY ticker, fiscal_year;

-- 2. Accruals: (net income - operating cash flow) / total assets
SELECT ticker, fiscal_year,
  (net_income - operating_cash_flow) / NULLIF(total_assets, 0) AS accruals_ratio,
  CASE
    WHEN ABS((net_income - operating_cash_flow) / NULLIF(total_assets, 0)) > 0.10 THEN 'RED'
    WHEN ABS((net_income - operating_cash_flow) / NULLIF(total_assets, 0)) > 0.05 THEN 'YELLOW'
    ELSE 'GREEN'
  END AS flag
FROM financials
ORDER BY ticker, fiscal_year;

-- 3. Share dilution year over year
SELECT a.ticker, a.fiscal_year,
  (a.shares_outstanding - b.shares_outstanding)
    / NULLIF(b.shares_outstanding, 0) AS dilution,
  CASE
    WHEN (a.shares_outstanding - b.shares_outstanding) / NULLIF(b.shares_outstanding, 0) > 0.05 THEN 'RED'
    WHEN (a.shares_outstanding - b.shares_outstanding) / NULLIF(b.shares_outstanding, 0) > 0.02 THEN 'YELLOW'
    ELSE 'GREEN'
  END AS flag
FROM financials a
JOIN financials b
  ON a.ticker = b.ticker AND a.fiscal_year = b.fiscal_year + 1
ORDER BY a.ticker, a.fiscal_year;

-- 4. Revenue growth year over year
SELECT a.ticker, a.fiscal_year,
  (a.revenue - b.revenue) / NULLIF(b.revenue, 0) AS revenue_growth
FROM financials a
JOIN financials b
  ON a.ticker = b.ticker AND a.fiscal_year = b.fiscal_year + 1
ORDER BY a.ticker, a.fiscal_year;

-- 5. Leverage: total liabilities / total assets
SELECT ticker, fiscal_year,
  total_liabilities / NULLIF(total_assets, 0) AS leverage,
  CASE
    WHEN total_liabilities / NULLIF(total_assets, 0) > 0.80 THEN 'RED'
    WHEN total_liabilities / NULLIF(total_assets, 0) > 0.60 THEN 'YELLOW'
    ELSE 'GREEN'
  END AS flag
FROM financials
ORDER BY ticker, fiscal_year;

-- 6. Latest-year summary: one row per ticker
SELECT ticker, MAX(fiscal_year) AS latest_year
FROM financials GROUP BY ticker;
