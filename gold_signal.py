#!/usr/bin/env python3
"""
gold_signal.py — read the local candle DB, produce signals, send Telegram alerts,
automatically log the signal to trade_journal.csv, and prevent whipsaw spam.
"""

import os
import sys
import json
import csv
import sqlite3
import argparse
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gold_data.db")
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_state.json")
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_journal.csv")

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

ACCOUNT_BALANCE = 100.0
LOT_SIZE = 0.01
DOLLAR_PER_POINT = 1.0

EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
ATR_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
SL_ATR_MULT = 1.5
TP_ATR_MULT = 2.5
MAX_RISK_WARNING_PCT = 3.0

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def load_candles(min_bars=60):
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM candles ORDER BY ts ASC", conn, parse_dates=["ts"])
    conn.close()
    if len(df) < min_bars:
        raise RuntimeError(f"Only {len(df)} candles in DB, need >= {min_bars}.")
    return df

def add_indicators(df):
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
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / ATR_PERIOD, adjust=False).mean()
    
    day = df["ts"].dt.date
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"].replace(0, np.nan)
    df["vwap"] = pv.groupby(day).cumsum() / df["volume"].replace(0, np.nan).groupby(day).cumsum()
    return df

def evaluate(df, mode="strict"):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    bull_cross = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
    bear_cross = prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]
    trend_up = last["ema_fast"] > last["ema_slow"]
    trend_down = last["ema_fast"] < last["ema_slow"]
    
    rsi_ok_buy = last["rsi"] < RSI_OVERBOUGHT
    rsi_ok_sell = last["rsi"] > RSI_OVERSOLD
    
    vwap_confirms_buy = pd.isna(last["vwap"]) or last["close"] >= last["vwap"]
    vwap_confirms_sell = pd.isna(last["vwap"]) or last["close"] <= last["vwap"]

    if mode == "strict":
        buy = bull_cross and rsi_ok_buy and vwap_confirms_buy
        sell = bear_cross and rsi_ok_sell and vwap_confirms_sell
    else:  
        buy = trend_up and rsi_ok_buy and last["rsi"] > 50
        sell = trend_down and rsi_ok_sell and last["rsi"] < 50

    bias = "BUY" if buy else ("SELL" if sell else "NONE")
    entry = last["close"]
    atr = last["atr"]
    
    if bias == "BUY":
        sl = entry - SL_ATR_MULT * atr
        tp = entry + TP_ATR_MULT * atr
    elif bias == "SELL":
        sl = entry + SL_ATR_MULT * atr
        tp = entry - TP_ATR_MULT * atr
    else:
        sl = tp = None

    risk_usd = 0.0
    risk_pct = 0.0
    if sl is not None:
        sl_distance = abs(entry - sl)
        risk_usd = sl_distance * DOLLAR_PER_POINT
        risk_pct = (risk_usd / ACCOUNT_BALANCE) * 100

    return {
        "ts": last["ts"], "close": entry, "ema_fast": last["ema_fast"], "ema_slow": last["ema_slow"],
        "rsi": last["rsi"], "atr": atr, "vwap": last["vwap"], "bias": bias, "sl": sl, "tp": tp,
        "mode": mode, "risk_usd": risk_usd, "risk_pct": risk_pct
    }

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f: return json.load(f)
        except json.JSONDecodeError:
            return {"last_signal_ts": None, "last_bias": None}
    return {"last_signal_ts": None, "last_bias": None}

def save_state(state):
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, 'w') as f: json.dump(state, f)
    os.replace(tmp_path, STATE_FILE)

def log_to_csv(r):
    file_exists = os.path.isfile(CSV_PATH)
    with open(CSV_PATH, mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Logged_At_UTC", "Signal_TS", "Bias", "Entry", "SL", "TP", "ATR", "RSI", "Risk_USD", "Risk_Pct", "Outcome", "Notes"])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(), r["ts"], r["bias"],
            f"{r['close']:.2f}", f"{r['sl']:.2f}", f"{r['tp']:.2f}",
            f"{r['atr']:.2f}", f"{r['rsi']:.1f}", f"{r['risk_usd']:.2f}",
            f"{r['risk_pct']:.1f}%", "PENDING", ""
        ])

def send_telegram(r):
    if not TG_TOKEN or not TG_CHAT_ID: return
    bias_emoji = "🟢" if r['bias'] == 'BUY' else "🔴"
    risk_warning = "⚠️ HIGH RISK" if r['risk_pct'] > MAX_RISK_WARNING_PCT else "✅ Risk OK"
    msg = (f"{bias_emoji} *XAU/USD 5m Signal [{r['mode'].upper()}]*\n\n"
           f"💰 *Entry:* `{r['close']:.2f}`\n🛑 *SL:* `{r['sl']:.2f}`\n🎯 *TP:* `{r['tp']:.2f}`\n\n"
           f"📊 *Indicators:*\nRSI: `{r['rsi']:.1f}` | ATR: `{r['atr']:.2f}`\n\n"
           f"⚖️ *Risk Check (0.01 lot / ${ACCOUNT_BALANCE} acct):*\n"
           f"SL Distance: `${abs(r['close'] - r['sl']):.2f}`\nRisk: `${r['risk_usd']:.2f}` ({r['risk_pct']:.1f}%)\nStatus: {risk_warning}")
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        if resp.status_code == 200: print("[INFO] Telegram alert sent.")
    except Exception as e: print(f"[WARN] Telegram failed: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["strict", "relaxed"], default="strict")
    args = parser.parse_args()

    df = load_candles(min_bars=max(EMA_SLOW, RSI_PERIOD, ATR_PERIOD) * 2)
    df = add_indicators(df)
    r = evaluate(df, mode=args.mode)

    print("=" * 50)
    print(f" XAU/USD 5m read — {r['ts']} UTC  [{r['mode']}]")
    print("=" * 50)
    print(f" BIAS      : {r['bias']}")
    if r["bias"] != "NONE":
        print(f" Entry ~   : {r['close']:.2f} | SL: {r['sl']:.2f} | TP: {r['tp']:.2f}")
        print(f" RISK      : ${r['risk_usd']:.2f} ({r['risk_pct']:.1f}%)")
    print("=" * 50)

    state = load_state()
    
    if r["bias"] != "NONE":
        # --- WHIPSAW COOLDOWN CHECK ---
        last_signal_ts_str = state.get("last_signal_ts")
        if last_signal_ts_str:
            # Parse the last signal time and current time to check the gap
            last_time = datetime.strptime(last_signal_ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            current_time = r["ts"].to_pydatetime()
            if current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=timezone.utc)
            
            minutes_since_last = (current_time - last_time).total_seconds() / 60
            
            # If a signal was sent less than 15 minutes ago, suppress the new one
            if minutes_since_last < 15:
                print(f"\n[INFO]  Cooldown active ({minutes_since_last:.1f} mins since last signal). Suppressing to avoid whipsaw.")
                state["last_bias"] = r["bias"] # Update bias to track trend, but don't alert
                save_state(state)
                sys.exit(0)
        # --------------------------------

        if r["ts"] != state.get("last_signal_ts") or r["bias"] != state.get("last_bias"):
            print("\n[INFO] New signal detected! Sending Telegram & Logging to CSV...")
            send_telegram(r)
            log_to_csv(r)
            
            state["last_signal_ts"] = r["ts"].strftime("%Y-%m-%dT%H:%M:%SZ")
            state["last_bias"] = r["bias"]
            save_state(state)
        else:
            print("\n[INFO] Signal already sent/logged for this candle.")
    else:
        if state.get("last_bias") is not None:
            state["last_bias"] = None
            save_state(state)

if __name__ == "__main__":
    main()
