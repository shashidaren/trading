#!/usr/bin/env python3
"""
analyze_trades.py — performance report from trade_journal.csv

Usage:
    python3 analyze_trades.py
    python3 analyze_trades.py --csv path/to/journal.csv

How to use the journal:
  1. Signals are auto-logged by gold_signal.py with Outcome = PENDING
  2. After the trade resolves, edit the CSV and set Outcome to:
       WIN   — hit take-profit first
       LOSS  — hit stop-loss first
       BE    — breakeven / scratched
  3. Optionally fill R_Multiple (e.g. 1.67 if you hit full TP at 2.5 ATR / 1.5 ATR risk)
  4. Run this script to see win rate, expectancy, etc.
"""

import os
import sys
import argparse
import pandas as pd

DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_journal.csv")


def load_journal(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        print(f"No journal found at: {path}")
        print("Run gold_signal.py a few times first so signals get logged.")
        sys.exit(1)

    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    return df


def summarise(df: pd.DataFrame):
    total = len(df)
    if total == 0:
        print("Journal is empty.")
        return

    print("=" * 60)
    print(" GOLD SIGNAL PERFORMANCE REPORT")
    print("=" * 60)
    print(f" Total signals logged : {total}")

    if "Outcome" not in df.columns:
        print("\n[WARN] No 'Outcome' column found. Old journal format?")
        return

    outcomes = df["Outcome"].astype(str).str.upper().str.strip()
    pending = (outcomes == "PENDING").sum()
    wins = (outcomes == "WIN").sum()
    losses = (outcomes == "LOSS").sum()
    be = (outcomes == "BE").sum()
    resolved = wins + losses + be

    print(f" Pending              : {pending}")
    print(f" Resolved             : {resolved}  (WIN={wins}  LOSS={losses}  BE={be})")

    if resolved == 0:
        print("\nNo resolved trades yet. Mark some Outcomes in the CSV and re-run.")
        print("=" * 60)
        return

    win_rate = wins / resolved * 100
    print(f"\n Win rate (resolved)  : {win_rate:.1f}%")

    if "R_Multiple" in df.columns:
        r_col = pd.to_numeric(df["R_Multiple"], errors="coerce")
        resolved_mask = outcomes.isin(["WIN", "LOSS", "BE"])
        r_resolved = r_col[resolved_mask].dropna()

        if len(r_resolved) > 0:
            avg_r = r_resolved.mean()
            print(f" Average R-Multiple   : {avg_r:+.2f}R")
            print(f" Expectancy           : {avg_r:+.2f}R per trade")

            pos = r_resolved[r_resolved > 0].sum()
            neg = abs(r_resolved[r_resolved < 0].sum())
            if neg > 0:
                print(f" Profit Factor        : {pos / neg:.2f}")
            else:
                print(" Profit Factor        : ∞ (no losing R recorded)")

    if "Mode" in df.columns:
        print("\n--- By Mode ---")
        for mode, group in df.groupby(df["Mode"].astype(str).str.lower()):
            g_out = group["Outcome"].astype(str).str.upper().str.strip()
            g_res = g_out.isin(["WIN", "LOSS", "BE"]).sum()
            g_wins = (g_out == "WIN").sum()
            if g_res > 0:
                print(f"  {mode:8s} : {g_wins}/{g_res} wins ({g_wins/g_res*100:.0f}%)  "
                      f"(total signals: {len(group)})")
            else:
                print(f"  {mode:8s} : no resolved trades yet  (total signals: {len(group)})")

    if "Bias" in df.columns:
        print("\n--- By Direction ---")
        for bias, group in df.groupby(df["Bias"].astype(str).str.upper()):
            g_out = group["Outcome"].astype(str).str.upper().str.strip()
            g_res = g_out.isin(["WIN", "LOSS", "BE"]).sum()
            g_wins = (g_out == "WIN").sum()
            if g_res > 0:
                print(f"  {bias:4s} : {g_wins}/{g_res} wins ({g_wins/g_res*100:.0f}%)")
            else:
                print(f"  {bias:4s} : no resolved trades yet")

    print("\n" + "=" * 60)
    print("Tip: After a trade closes, edit trade_journal.csv and set")
    print("     Outcome = WIN / LOSS / BE and optionally fill R_Multiple.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Analyse gold signal journal")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Path to trade_journal.csv")
    args = parser.parse_args()

    df = load_journal(args.csv)
    summarise(df)


if __name__ == "__main__":
    main()
