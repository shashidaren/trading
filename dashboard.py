#!/usr/bin/env python3
"""
dashboard.py — Gold (XAU/USD) trading dashboard.

A lightweight Flask app that reads the same candle DB / journal / signal logic
as the rest of the repo and exposes a clean web UI + JSON API.

    python3 dashboard.py                # serve live data (needs gold_data.db)
    python3 dashboard.py --demo         # serve synthetic data (no DB required)
    python3 dashboard.py --port 5000 --no-open

Endpoints:
    GET /                dashboard UI
    GET /api/overview    price, signal bias, indicators, filters, risk, state
    GET /api/candles     OHLCV + EMA/RSI/ATR/VWAP series for charting
    GET /api/trades      journal + performance stats + equity curve
    GET /api/news        gold news headlines
    GET /api/calendar    economic calendar
"""

import os
import json
import math
import argparse
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template

# Reuse the exact signal/indicator logic from gold_signal.py (single source of truth)
import gold_signal as gs
import news_provider

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "gold_data.db")
CSV_PATH = os.path.join(BASE_DIR, "trade_journal.csv")
STATE_FILE = os.path.join(BASE_DIR, "signal_state.json")

SYMBOL = "XAU/USD"
app = Flask(__name__)

DEMO = False
_demo_candles = None       # cached synthetic dataframe
_demo_trades = None        # cached synthetic journal rows


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_candles_df():
    """Return candles dataframe (ts, open, high, low, close, volume). Empty-safe."""
    if DEMO:
        return _demo_candles.copy()
    if not os.path.isfile(DB_PATH):
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    try:
        conn = gs.get_conn()
        df = pd.read_sql_query("SELECT * FROM candles ORDER BY ts ASC", conn, parse_dates=["ts"])
        conn.close()
        return df
    except Exception as e:  # noqa: BLE001
        app.logger.warning("load_candles_df failed: %s", e)
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])


def load_journal_df():
    if DEMO:
        return pd.DataFrame(_demo_trades)
    if not os.path.isfile(CSV_PATH):
        return pd.DataFrame()
    try:
        df = pd.read_csv(CSV_PATH)
        df.columns = [c.strip() for c in df.columns]
        return df
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Demo data (synthetic but realistic, so the UI is reviewable without a DB)
# ---------------------------------------------------------------------------
def _generate_demo_candles(n=1400, seed=7):
    rng = np.random.default_rng(seed)
    start = 2650.0
    # start ~4 days back from "now"
    end_ts = pd.Timestamp.now(tz="UTC").floor("5min")
    idx = pd.date_range(end=end_ts, periods=n, freq="5min")

    closes = np.empty(n)
    closes[0] = start
    trend = 0.0
    for i in range(1, n):
        if i % 140 == 0:
            trend = rng.choice([-1, -1, 1, 1, 0]) * 0.05   # regime shifts
        vol = rng.uniform(0.6, 2.0)
        closes[i] = closes[i - 1] + trend + rng.normal(0, vol)

    closes = np.maximum(closes, 2000.0)

    # Craft a clean bullish tail so the demo shows a live BUY signal:
    # decline -> flat consolidation (EMAs converge, RSI resets) ->
    # one modest up bar = fresh EMA cross without overbought RSI.
    t0 = n - 72
    base = closes[t0]
    for i in range(t0, n - 42):                      # slow decline
        closes[i] = base - (i - t0) * 0.35 + rng.normal(0, 0.5)
    for i in range(n - 42, n - 1):                   # flat base
        closes[i] = closes[n - 43] + rng.normal(0, 0.2)
    closes[n - 1] = closes[n - 2] + 2.0              # break higher (final bar)

    opens = np.roll(closes, 1)
    opens[0] = closes[0] - rng.normal(0, 1)
    body = np.abs(closes - opens)
    highs = np.maximum(opens, closes) + rng.uniform(0.2, 2.2, n)
    lows = np.minimum(opens, closes) - rng.uniform(0.2, 2.2, n)
    volume = rng.uniform(100, 900, n)

    df = pd.DataFrame({
        "ts": idx, "open": opens, "high": highs,
        "low": lows, "close": closes, "volume": volume,
    })

    # Nudge the final close above VWAP so the VWAP confirmation also passes
    # (price is slightly below the session average after the decline above).
    for _ in range(8):
        ind = gs.add_indicators(df)
        last = ind.iloc[-1]
        prev = ind.iloc[-2]
        bull = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
        rsi_ok = last["rsi"] < gs.RSI_OVERBOUGHT
        if not (bull and rsi_ok):
            break
        if last["close"] >= last["vwap"]:
            break
        new_close = float(last["vwap"]) + 0.4
        i = df.index[-1]
        df.at[i, "close"] = new_close
        df.at[i, "high"] = max(df.at[i, "high"], new_close + 0.2)

    return df


def _generate_demo_trades(seed=11):
    rng = np.random.default_rng(seed)
    rows = []
    end_ts = pd.Timestamp.now(tz="UTC")
    # realistic-ish outcomes: ~48% win, avg R slightly positive on winners
    for i in range(24):
        bias = "BUY" if rng.random() < 0.5 else "SELL"
        outcome = rng.choice(["WIN", "LOSS", "LOSS", "BE", "PENDING"], p=[0.38, 0.40, 0.06, 0.08, 0.08])
        if outcome == "WIN":
            r = round(rng.uniform(1.1, 2.5), 2)
        elif outcome == "LOSS":
            r = -1.0
        else:
            r = 0.0
        ts = end_ts - pd.Timedelta(hours=i * 9 + rng.integers(0, 6))
        rows.append({
            "Logged_At_UTC": ts.isoformat(),
            "Signal_TS": ts.strftime("%Y-%m-%d %H:%M:%S%z"),
            "Bias": bias, "Mode": "strict",
            "Entry": round(rng.uniform(2620, 2690), 2),
            "SL": 0.0, "TP": 0.0, "ATR": round(rng.uniform(2, 6), 2),
            "RSI": round(rng.uniform(40, 60), 1),
            "Risk_USD": round(rng.uniform(1, 3), 2),
            "Risk_Pct": "1.5%",
            "Outcome": outcome, "R_Multiple": r, "Notes": "",
        })
    rows.sort(key=lambda x: x["Signal_TS"])
    return rows


def init_demo():
    global _demo_candles, _demo_trades
    _demo_candles = _generate_demo_candles()
    _demo_trades = _generate_demo_trades()


# ---------------------------------------------------------------------------
# Computations
# ---------------------------------------------------------------------------
def compute_signal(df):
    """Run the real signal logic. Returns overview dict or None if no data."""
    if len(df) < gs.EMA_SLOW + 2:
        return None
    try:
        ind = gs.add_indicators(df)
        strict = gs.evaluate(ind, mode="strict")
        relaxed = gs.evaluate(ind, mode="relaxed")
    except Exception as e:  # noqa: BLE001
        app.logger.warning("compute_signal failed: %s", e)
        return None

    last = ind.iloc[-1]
    return {
        "strict": strict,
        "relaxed": relaxed,
        "last": last,
    }


def overview():
    df = load_candles_df()
    out = {
        "symbol": SYMBOL,
        "source": "demo" if DEMO else "live",
        "has_data": len(df) > 0,
        "bars": int(len(df)),
        "last_candle_ts": None,
        "price": None,
        "change_5m": None,
        "change_24h": None,
        "bias": None,
        "bias_relaxed": None,
        "entry": None, "sl": None, "tp": None,
        "rsi": None, "atr": None, "vwap": None,
        "ema_fast": None, "ema_slow": None,
        "atr_pctl": None,
        "session_ok": None, "vol_ok": None,
        "filter_reason": None,
        "risk_usd": None, "risk_pct": None,
        "state": {},
    }

    if len(df) == 0:
        return out

    last = df.iloc[-1]
    out["last_candle_ts"] = last["ts"].isoformat()
    out["price"] = round(float(last["close"]), 2)
    if len(df) >= 2:
        out["change_5m"] = round(float(last["close"] - df.iloc[-2]["close"]), 2)

    # 24h change (~288 5m bars)
    if len(df) >= 288:
        out["change_24h"] = round(float(last["close"] - df.iloc[-289]["close"]), 2)

    sig = compute_signal(df)
    if sig:
        r = sig["strict"]
        out["bias"] = r["bias"]
        out["bias_relaxed"] = sig["relaxed"]["bias"]
        out["entry"] = r["close"]
        out["sl"] = r["sl"]
        out["tp"] = r["tp"]
        out["rsi"] = r["rsi"]
        out["atr"] = r["atr"]
        out["vwap"] = r["vwap"]
        out["ema_fast"] = r["ema_fast"]
        out["ema_slow"] = r["ema_slow"]
        out["atr_pctl"] = r["atr_pctl"]
        out["session_ok"] = r["session_ok"]
        out["vol_ok"] = r["vol_ok"]
        out["filter_reason"] = r["filter_reason"]
        out["risk_usd"] = r["risk_usd"]
        out["risk_pct"] = r["risk_pct"]

    # anti-flip / cooldown state
    state = gs.load_state()
    now = datetime.now(timezone.utc)
    last_wall = gs.parse_iso_utc(state.get("last_alert_wall_ts"))
    mins_since = None
    if last_wall:
        mins_since = round((now - last_wall).total_seconds() / 60.0, 1)
    out["state"] = {
        "last_bias": state.get("last_bias"),
        "last_signal_ts": state.get("last_signal_ts"),
        "mins_since_alert": mins_since,
        "cooldown_minutes": gs.COOLDOWN_MINUTES,
        "opposite_cooldown_minutes": gs.OPPOSITE_COOLDOWN_MINUTES,
    }
    return out


def candles_series(bars=400):
    df = load_candles_df()
    if len(df) == 0:
        return {"candles": [], "rsi": [], "atr": [], "vwap": [], "ema_fast": [], "ema_slow": []}

    df = df.tail(bars).copy()
    ind = gs.add_indicators(df)
    ind = ind.reset_index(drop=True)

    def _t(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    candles = [
        {
            "time": _t(row["ts"]),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
            "volume": float(row["volume"]) if not pd.isna(row["volume"]) else 0.0,
        }
        for _, row in ind.iterrows()
    ]

    def _series(col, nd=3):
        return [None if pd.isna(v) else round(float(v), nd) for v in ind[col]]

    return {
        "candles": candles,
        "rsi": _series("rsi", 2),
        "atr": _series("atr", 2),
        "vwap": _series("vwap", 2),
        "ema_fast": _series("ema_fast", 2),
        "ema_slow": _series("ema_slow", 2),
    }


def trades_stats():
    df = load_journal_df()
    empty = {
        "has_journal": False, "total": 0, "pending": 0, "resolved": 0,
        "wins": 0, "losses": 0, "be": 0, "win_rate": None, "avg_r": None,
        "expectancy": None, "profit_factor": None, "final_r": None,
        "max_drawdown": None, "max_win_streak": 0, "max_loss_streak": 0,
        "equity_curve": [], "trades": [], "by_mode": [], "by_bias": [],
    }
    if df is None or len(df) == 0 or "Outcome" not in df.columns:
        return empty

    df = df.copy()
    df["Outcome"] = df["Outcome"].astype(str).str.upper().str.strip()
    out = dict(empty)
    out["has_journal"] = True
    out["total"] = int(len(df))
    out["pending"] = int((df["Outcome"] == "PENDING").sum())

    resolved = df[df["Outcome"].isin(["WIN", "LOSS", "BE"])]
    out["resolved"] = int(len(resolved))
    out["wins"] = int((resolved["Outcome"] == "WIN").sum())
    out["losses"] = int((resolved["Outcome"] == "LOSS").sum())
    out["be"] = int((resolved["Outcome"] == "BE").sum())

    if len(resolved) > 0:
        out["win_rate"] = round(out["wins"] / len(resolved) * 100, 1)

        if "R_Multiple" in df.columns:
            r = pd.to_numeric(resolved["R_Multiple"], errors="coerce")
            r_valid = r.dropna()
            if len(r_valid) > 0:
                out["avg_r"] = round(float(r_valid.mean()), 3)
                out["expectancy"] = out["avg_r"]
                pos = float(r_valid[r_valid > 0].sum())
                neg = abs(float(r_valid[r_valid < 0].sum()))
                out["profit_factor"] = round(pos / neg, 2) if neg > 0 else None
                # equity curve in signal order
                if "Signal_TS" in resolved.columns:
                    srt = resolved.assign(_r=r).sort_values("Signal_TS")
                    curve = []
                    cum = 0.0
                    peak = 0.0
                    mdd = 0.0
                    for v in srt["_r"].dropna():
                        cum += v
                        curve.append(round(cum, 2))
                        peak = max(peak, cum)
                        mdd = min(mdd, cum - peak)
                    out["equity_curve"] = curve
                    out["final_r"] = round(cum, 2)
                    out["max_drawdown"] = round(mdd, 2)

        # streaks (on resolved outcomes, in signal order)
        if "Signal_TS" in resolved.columns:
            seq = resolved.sort_values("Signal_TS")["Outcome"].tolist()
        else:
            seq = resolved["Outcome"].tolist()
        win_streak = loss_streak = 0
        best_w = best_l = 0
        for o in seq:
            if o == "WIN":
                win_streak += 1
                loss_streak = 0
                best_w = max(best_w, win_streak)
            elif o == "LOSS":
                loss_streak += 1
                win_streak = 0
                best_l = max(best_l, loss_streak)
        out["max_win_streak"] = best_w
        out["max_loss_streak"] = best_l

    # by mode / bias
    if "Mode" in df.columns:
        by_mode = []
        for mode, g in df.groupby(df["Mode"].astype(str).str.lower()):
            gres = g[g["Outcome"].isin(["WIN", "LOSS", "BE"])]
            gw = int((gres["Outcome"] == "WIN").sum())
            by_mode.append({
                "mode": mode, "total": int(len(g)),
                "resolved": int(len(gres)), "wins": gw,
                "win_rate": round(gw / len(gres) * 100, 1) if len(gres) else None,
            })
        out["by_mode"] = by_mode

    if "Bias" in df.columns:
        by_bias = []
        for bias, g in df.groupby(df["Bias"].astype(str).str.upper()):
            gres = g[g["Outcome"].isin(["WIN", "LOSS", "BE"])]
            gw = int((gres["Outcome"] == "WIN").sum())
            by_bias.append({
                "bias": bias, "total": int(len(g)),
                "resolved": int(len(gres)), "wins": gw,
                "win_rate": round(gw / len(gres) * 100, 1) if len(gres) else None,
            })
        out["by_bias"] = by_bias

    # trades list (most recent first) for the table
    cols = ["Signal_TS", "Bias", "Mode", "Entry", "SL", "TP", "Outcome", "R_Multiple"]
    table = df[[c for c in cols if c in df.columns]].copy()
    table = table.sort_values("Signal_TS", ascending=False) if "Signal_TS" in table.columns else table
    out["trades"] = table.head(50).where(pd.notnull(table.head(50)), None).to_dict(orient="records")
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", symbol=SYMBOL)


@app.route("/api/overview")
def api_overview():
    return jsonify(overview())


@app.route("/api/candles")
def api_candles():
    bars = min(int(__import__("flask").request.args.get("bars", 400)), 1000)
    return jsonify(candles_series(bars))


@app.route("/api/trades")
def api_trades():
    return jsonify(trades_stats())


@app.route("/api/news")
def api_news():
    items = news_provider.fetch_news()
    status = news_provider.news_status()
    return jsonify({
        "items": items,
        "live": len(items) > 0,
        "fallback_sources": news_provider.FALLBACK_SOURCES if not items else [],
        "cache_ts": status["cache_ts"],
        "now": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/calendar")
def api_calendar():
    return jsonify({"events": news_provider.get_calendar()})


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "demo": DEMO, "ts": datetime.now(timezone.utc).isoformat()})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    global DEMO
    parser = argparse.ArgumentParser(description="Gold trading dashboard")
    parser.add_argument("--demo", action="store_true", help="Serve synthetic data (no DB needed)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5000")))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    DEMO = args.demo
    if DEMO:
        init_demo()
        print("[dashboard] DEMO mode — serving synthetic candles & journal.")

    print(f"[dashboard] Serving on http://{args.host}:{args.port}  (demo={DEMO})")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
