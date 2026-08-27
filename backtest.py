#!/usr/bin/env python3
"""
backtest.py — simple event-driven backtest of the gold signal rules

Uses the exact same indicator & signal logic as gold_signal.py so results
are consistent with live behaviour.

Usage:
    python3 backtest.py
    python3 backtest.py --mode relaxed
    python3 backtest.py --mode strict --sl 1.5 --tp 2.5
    python3 backtest.py --min-bars 300
"""

import os
import sys
import sqlite3
import argparse
from dataclasses import dataclass
from typing import Optional, List

import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gold_data.db")

# Defaults matching gold_signal.py
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
ATR_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
DEFAULT_SL_ATR = 1.5
DEFAULT_TP_ATR = 2.5


@dataclass
class Trade:
    entry_ts: pd.Timestamp
    bias: str
    entry: float
    sl: float
    tp: float
    atr: float
    exit_ts: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    outcome: Optional[str] = None  # WIN / LOSS / BE
    r_multiple: Optional[float] = None


def load_candles(min_bars: int = 100) -> pd.DataFrame:
    if not os.path.isfile(DB_PATH):
        print("No gold_data.db found. Run collector.py --backfill 500 first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT * FROM candles ORDER BY ts ASC",
        conn,
        parse_dates=["ts"],
    )
    conn.close()

    if len(df) < min_bars:
        print(f"Only {len(df)} candles available, need at least {min_bars}.")
        sys.exit(1)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / ATR_PERIOD, adjust=False).mean()

    day = df["ts"].dt.date
    typical = (df["high"] + df["low"] + df["close"]) / 3
    volume = df["volume"].replace(0, np.nan)
    pv = typical * volume
    df["vwap"] = pv.groupby(day).cumsum() / volume.groupby(day).cumsum()

    return df


def signal_at(i: int, df: pd.DataFrame, mode: str) -> Optional[str]:
    """Return 'BUY', 'SELL', or None for bar index i (needs i >= 1)."""
    if i < 1:
        return None

    last = df.iloc[i]
    prev = df.iloc[i - 1]

    if pd.isna(last["ema_fast"]) or pd.isna(last["ema_slow"]) or pd.isna(last["rsi"]) or pd.isna(last["atr"]):
        return None

    bull_cross = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
    bear_cross = prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]
    trend_up = last["ema_fast"] > last["ema_slow"]
    trend_down = last["ema_fast"] < last["ema_slow"]

    rsi_ok_buy = last["rsi"] < RSI_OVERBOUGHT
    rsi_ok_sell = last["rsi"] > RSI_OVERSOLD

    vwap_confirms_buy = pd.isna(last["vwap"]) or last["close"] >= last["vwap"]
    vwap_confirms_sell = pd.isna(last["vwap"]) or last["close"] <= last["vwap"]

    if mode == "strict":
        if bull_cross and rsi_ok_buy and vwap_confirms_buy:
            return "BUY"
        if bear_cross and rsi_ok_sell and vwap_confirms_sell:
            return "SELL"
    else:  # relaxed
        if trend_up and rsi_ok_buy and last["rsi"] > 50:
            return "BUY"
        if trend_down and rsi_ok_sell and last["rsi"] < 50:
            return "SELL"

    return None


def run_backtest(
    df: pd.DataFrame,
    mode: str = "strict",
    sl_atr: float = DEFAULT_SL_ATR,
    tp_atr: float = DEFAULT_TP_ATR,
    cooldown_bars: int = 3,  # ~15 min on 5m
) -> List[Trade]:
    trades: List[Trade] = []
    open_trade: Optional[Trade] = None
    last_signal_i = -999

    # Start after indicators are warm
    start = max(EMA_SLOW, RSI_PERIOD, ATR_PERIOD) + 5

    for i in range(start, len(df)):
        bar = df.iloc[i]

        # Manage open trade first
        if open_trade is not None:
            high = bar["high"]
            low = bar["low"]
            bias = open_trade.bias
            hit_sl = hit_tp = False

            if bias == "BUY":
                hit_sl = low <= open_trade.sl
                hit_tp = high >= open_trade.tp
            else:
                hit_sl = high >= open_trade.sl
                hit_tp = low <= open_trade.tp

            if hit_sl and hit_tp:
                # Conservative: same bar → LOSS
                open_trade.outcome = "LOSS"
                open_trade.exit_price = open_trade.sl
                open_trade.r_multiple = -1.0
                open_trade.exit_ts = bar["ts"]
                trades.append(open_trade)
                open_trade = None
            elif hit_tp:
                risk = abs(open_trade.entry - open_trade.sl)
                open_trade.outcome = "WIN"
                open_trade.exit_price = open_trade.tp
                open_trade.r_multiple = round(abs(open_trade.tp - open_trade.entry) / risk, 2) if risk > 0 else 0.0
                open_trade.exit_ts = bar["ts"]
                trades.append(open_trade)
                open_trade = None
            elif hit_sl:
                open_trade.outcome = "LOSS"
                open_trade.exit_price = open_trade.sl
                open_trade.r_multiple = -1.0
                open_trade.exit_ts = bar["ts"]
                trades.append(open_trade)
                open_trade = None

            # If still open, don't take a new signal
            if open_trade is not None:
                continue

        # Look for new signal
        if i - last_signal_i < cooldown_bars:
            continue

        bias = signal_at(i, df, mode)
        if bias is None:
            continue

        atr = float(bar["atr"])
        if atr <= 0 or pd.isna(atr):
            continue

        entry = float(bar["close"])
        if bias == "BUY":
            sl = entry - sl_atr * atr
            tp = entry + tp_atr * atr
        else:
            sl = entry + sl_atr * atr
            tp = entry - tp_atr * atr

        open_trade = Trade(
            entry_ts=bar["ts"],
            bias=bias,
            entry=entry,
            sl=sl,
            tp=tp,
            atr=atr,
        )
        last_signal_i = i

    # Leave any still-open trade unresolved (not counted)
    return trades


def print_report(trades: List[Trade], mode: str, sl_atr: float, tp_atr: float, total_bars: int):
    print("=" * 60)
    print(" BACKTEST REPORT — XAU/USD 5m")
    print("=" * 60)
    print(f" Mode              : {mode}")
    print(f" SL / TP (ATR)     : {sl_atr} / {tp_atr}")
    print(f" Bars tested       : {total_bars}")
    print(f" Total trades      : {len(trades)}")

    if not trades:
        print("\nNo trades generated. Try --mode relaxed or more history.")
        print("=" * 60)
        return

    wins = [t for t in trades if t.outcome == "WIN"]
    losses = [t for t in trades if t.outcome == "LOSS"]
    resolved = len(wins) + len(losses)

    win_rate = len(wins) / resolved * 100 if resolved else 0.0
    r_list = [t.r_multiple for t in trades if t.r_multiple is not None]
    avg_r = float(np.mean(r_list)) if r_list else 0.0

    print(f" Wins / Losses     : {len(wins)} / {len(losses)}")
    print(f" Win rate          : {win_rate:.1f}%")
    print(f" Average R         : {avg_r:+.2f}R")
    print(f" Expectancy        : {avg_r:+.2f}R per trade")

    pos = sum(r for r in r_list if r > 0)
    neg = abs(sum(r for r in r_list if r < 0))
    if neg > 0:
        print(f" Profit Factor     : {pos / neg:.2f}")
    else:
        print(" Profit Factor     : ∞")

    # By direction
    for bias in ("BUY", "SELL"):
        subset = [t for t in trades if t.bias == bias]
        if not subset:
            continue
        w = sum(1 for t in subset if t.outcome == "WIN")
        print(f" {bias:4s} trades      : {len(subset)}  (wins: {w}, win rate: {w/len(subset)*100:.0f}%)")

    # Simple equity curve stats (in R)
    equity = np.cumsum(r_list)
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    max_dd = float(dd.min()) if len(dd) else 0.0
    print(f" Max Drawdown (R)  : {max_dd:.2f}R")
    print(f" Final R           : {equity[-1]:+.2f}R" if len(equity) else " Final R           : 0.00R")

    print("=" * 60)
    print("Note: This is a simplified backtest (no spread/slippage).")
    print("Use it to compare rules, not as a guarantee of live results.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Backtest gold signal rules")
    parser.add_argument("--mode", choices=["strict", "relaxed"], default="strict")
    parser.add_argument("--sl", type=float, default=DEFAULT_SL_ATR, help="SL ATR multiple")
    parser.add_argument("--tp", type=float, default=DEFAULT_TP_ATR, help="TP ATR multiple")
    parser.add_argument("--min-bars", type=int, default=100)
    parser.add_argument("--cooldown", type=int, default=3, help="Min bars between new signals")
    args = parser.parse_args()

    df = load_candles(min_bars=args.min_bars)
    df = add_indicators(df)

    trades = run_backtest(
        df,
        mode=args.mode,
        sl_atr=args.sl,
        tp_atr=args.tp,
        cooldown_bars=args.cooldown,
    )

    print_report(trades, args.mode, args.sl, args.tp, len(df))


if __name__ == "__main__":
    main()
