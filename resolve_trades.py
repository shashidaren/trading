#!/usr/bin/env python3
"""
resolve_trades.py — automatically mark PENDING trades as WIN / LOSS / BE

Logic:
  - Looks at candles *after* the signal timestamp
  - Checks whether price hit Take-Profit or Stop-Loss first
  - Updates trade_journal.csv in place

Usage:
    python3 resolve_trades.py
    python3 resolve_trades.py --dry-run          # show what would change, don't write
    python3 resolve_trades.py --max-bars 100     # look at most 100 bars ahead (default 200)
"""

import os
import sys
import csv
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gold_data.db")
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_journal.csv")


def load_candles():
    if not os.path.isfile(DB_PATH):
        print("No gold_data.db found. Run collector.py first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT ts, open, high, low, close FROM candles ORDER BY ts ASC",
        conn,
        parse_dates=["ts"],
    )
    conn.close()
    return df


def load_journal():
    if not os.path.isfile(CSV_PATH):
        print("No trade_journal.csv found yet.")
        sys.exit(1)
    df = pd.read_csv(CSV_PATH)
    df.columns = [c.strip() for c in df.columns]
    return df


def parse_ts(ts_str):
    """Try several common timestamp formats."""
    ts_str = str(ts_str).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            dt = datetime.strptime(ts_str.replace("+0000", "+00:00"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    # last resort
    try:
        return pd.to_datetime(ts_str, utc=True).to_pydatetime()
    except Exception:
        return None


def resolve_one(row, candles: pd.DataFrame, max_bars: int):
    """
    Returns (outcome, r_multiple) or (None, None) if still open / can't resolve.
    """
    bias = str(row.get("Bias", "")).upper().strip()
    if bias not in ("BUY", "SELL"):
        return None, None

    try:
        entry = float(row["Entry"])
        sl = float(row["SL"])
        tp = float(row["TP"])
    except (KeyError, ValueError, TypeError):
        return None, None

    signal_ts = parse_ts(row.get("Signal_TS", ""))
    if signal_ts is None:
        return None, None

    # Candles strictly after the signal bar
    future = candles[candles["ts"] > signal_ts].head(max_bars)
    if future.empty:
        return None, None  # not enough future data yet

    risk = abs(entry - sl)
    if risk <= 0:
        return None, None

    for _, bar in future.iterrows():
        high = bar["high"]
        low = bar["low"]

        if bias == "BUY":
            hit_sl = low <= sl
            hit_tp = high >= tp
        else:  # SELL
            hit_sl = high >= sl
            hit_tp = low <= tp

        if hit_sl and hit_tp:
            # Both hit in same bar — treat as LOSS (conservative)
            return "LOSS", -1.0
        if hit_tp:
            r_mult = abs(tp - entry) / risk
            return "WIN", round(r_mult, 2)
        if hit_sl:
            return "LOSS", -1.0

    return None, None  # still open


def main():
    parser = argparse.ArgumentParser(description="Auto-resolve PENDING trades")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--max-bars", type=int, default=200,
                        help="Max bars to look ahead (default 200 ≈ 16 hours on 5m)")
    args = parser.parse_args()

    candles = load_candles()
    journal = load_journal()

    if "Outcome" not in journal.columns:
        print("Journal is in old format (no Outcome column).")
        print("New signals will use the correct format. You can delete the old journal if you want a clean start:")
        print("  mv trade_journal.csv trade_journal_old.csv")
        sys.exit(0)

    outcomes = journal["Outcome"].astype(str).str.upper().str.strip()
    pending_mask = outcomes == "PENDING"
    pending_count = pending_mask.sum()

    if pending_count == 0:
        print("No PENDING trades to resolve.")
        return

    print(f"Found {pending_count} PENDING trade(s). Checking...")

    updated = 0
    for idx in journal[pending_mask].index:
        row = journal.loc[idx]
        outcome, r_mult = resolve_one(row, candles, args.max_bars)

        if outcome is None:
            print(f"  [{row.get('Signal_TS')}] {row.get('Bias')} still open")
            continue

        print(f"  [{row.get('Signal_TS')}] {row.get('Bias')} → {outcome}  (R={r_mult})")

        if not args.dry_run:
            journal.at[idx, "Outcome"] = outcome
            if "R_Multiple" in journal.columns:
                journal.at[idx, "R_Multiple"] = r_mult
            updated += 1

    if args.dry_run:
        print("\nDry-run only — no changes written.")
    elif updated:
        # Write back safely
        tmp = CSV_PATH + ".tmp"
        journal.to_csv(tmp, index=False)
        os.replace(tmp, CSV_PATH)
        print(f"\nUpdated {updated} trade(s) in trade_journal.csv")
    else:
        print("\nNo trades resolved this run.")


if __name__ == "__main__":
    main()
