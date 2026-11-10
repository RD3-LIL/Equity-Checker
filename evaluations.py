"""
Stock Evaluator scores, saved in the same SQLite database as the
EDGAR financials and bullshit checks (equity.db).

Framework is frozen: Quality, Growth, Valuation, Moat, Risk,
each 1-5. Total = round(sum / 25 * 100). Do not add factors.
Stance is the human call: Own, Watch, or Avoid.
One row per ticker per date, so a re-score is history, not an overwrite.

Usage:
    python3 evaluations.py list
    python3 evaluations.py show GOOGL
    python3 evaluations.py add AAPL 5 4 4 5 3 --stance Own --date 2026-11-09 --notes "Strong moat, fair valuation"
    python3 evaluations.py seed   # load the 7 saved Evaluator evaluations
"""

import argparse
import sqlite3
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "equity.db"

# The 7 saved Stock Evaluator evaluations, with exact factor scores
# from the Evaluator (Oct 2026). These are the source of truth, do not edit.
SEED = [
    # ticker, date, Q, G, V, M, R, stance, notes
    ("GOOGL", "2026-10-11", 5, 4, 4, 5, 3, "Own", "Saved Oct 11, 2026"),
    ("AMZN", "2026-10-11", 4, 5, 4, 5, 3, "Own", "Saved Oct 11, 2026"),
    ("NXPI", "2026-10-11", 4, 3, 5, 5, 3, "Own", "Saved Oct 11, 2026"),
    ("LMT", "2026-10-11", 4, 2, 4, 5, 4, "Own", "Saved Oct 11, 2026"),
    ("MU", "2026-10-11", 4, 5, 3, 3, 2, "Own", "Saved Oct 11, 2026, smallest position, cyclical risk"),
    ("HOOD", "2026-10-10", 4, 4, 2, 3, 2, "Watch", "Saved Oct 10, 2026, valuation and volatility, better near $90"),
    ("SPCX", "2026-10-20", 4, 5, 1, 5, 2, "Watch", "Saved Oct 20, 2026, widest moat and strongest growth, killed on valuation"),
]

def total_for(q, g, v, m, r):
    return round((q + g + v + m + r) / 25 * 100)

def ensure_table(conn):
    conn.execute("""
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
        )
    """)

def add(conn, ticker, q, g, v, m, r, stance, eval_date, notes):
    total = total_for(q, g, v, m, r)
    conn.execute(
        "INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ticker.upper(), eval_date, q, g, v, m, r, total, stance, notes),
    )
    conn.commit()
    print(f"{ticker.upper()} {eval_date}: Q{q} G{g} V{v} M{m} R{r} = {total}/100, {stance}")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("seed")
    show = sub.add_parser("show")
    show.add_argument("ticker")
    add_p = sub.add_parser("add")
    add_p.add_argument("ticker")
    add_p.add_argument("quality", type=int)
    add_p.add_argument("growth", type=int)
    add_p.add_argument("valuation", type=int)
    add_p.add_argument("moat", type=int)
    add_p.add_argument("risk", type=int)
    add_p.add_argument("--stance", default="Watch", choices=["Own", "Watch", "Avoid"])
    add_p.add_argument("--date", default=str(date.today()))
    add_p.add_argument("--notes", default="")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    ensure_table(conn)

    if args.cmd == "seed":
        for row in SEED:
            ticker, d, q, g, v, m, r, stance, notes = row
            add(conn, ticker, q, g, v, m, r, stance, d, notes)
        print("Seeded 7 saved evaluations.")
    elif args.cmd == "list":
        rows = conn.execute(
            "SELECT ticker, eval_date, quality, growth, valuation, moat, risk, total, stance "
            "FROM evaluations ORDER BY total DESC, ticker"
        ).fetchall()
        if not rows:
            print("No evaluations yet. Run: python3 evaluations.py seed")
        for t, d, q, g, v, m, r, total, stance in rows:
            print(f"{t:<6} {d}  Q{q} G{g} V{v} M{m} R{r}  {total:>3}/100  {stance}")
    elif args.cmd == "show":
        rows = conn.execute(
            "SELECT eval_date, quality, growth, valuation, moat, risk, total, stance, notes "
            "FROM evaluations WHERE ticker = ? ORDER BY eval_date",
            (args.ticker.upper(),),
        ).fetchall()
        if not rows:
            print(f"No evaluation for {args.ticker.upper()}")
        for d, q, g, v, m, r, total, stance, notes in rows:
            print(f"{args.ticker.upper()} {d}: Q{q} G{g} V{v} M{m} R{r} = {total}/100, {stance}")
            if notes:
                print(f"  {notes}")
        # Join to the bullshit checker for the same ticker, if it has EDGAR data
        try:
            flags = conn.execute(
                "SELECT fiscal_year, check_name, flag FROM checks WHERE ticker = ? AND flag = 'RED'",
                (args.ticker.upper(),),
            ).fetchall()
        except Exception:
            flags = []
            print("  Note: no checks table yet, run python3 equity_checker.py first to build the EDGAR data.")
        if flags:
            print("  Bullshit checker RED flags (veto, not a score change):")
            for fy, name, flag in flags:
                print(f"    FY{fy} {name}")
    elif args.cmd == "add":
        for name, val in [("quality", args.quality), ("growth", args.growth),
                          ("valuation", args.valuation), ("moat", args.moat), ("risk", args.risk)]:
            if not 1 <= val <= 5:
                raise SystemExit(f"{name} must be 1-5, got {val}")
        add(conn, args.ticker, args.quality, args.growth, args.valuation,
            args.moat, args.risk, args.stance, args.date, args.notes)
    conn.close()

if __name__ == "__main__":
    main()
