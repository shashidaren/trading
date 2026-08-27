# Gold Signal System — Roadmap & Recovery Guide

Last updated: 2026-08-27

This file is the single reference point for where the project stands and what to do next.

---

## Current Status (Working)

| Component | Status | Notes |
|-----------|--------|-------|
| `collector.py` | ✅ Working | Pulls 5m XAU/USD from Twelve Data → SQLite |
| `gold_signal.py` | ✅ Working | EMA9/21 + RSI + ATR + VWAP signals, Telegram, journal |
| `resolve_trades.py` | ✅ Working | Auto-marks PENDING → WIN/LOSS/BE |
| `analyze_trades.py` | ✅ Working | Performance report from journal |
| `backtest.py` | ✅ Working | Same rules as live, quick rule testing |
| Cron (every 5 min) | ✅ Working | collector → gold_signal |
| `.env` secrets | ✅ Local only | Never committed |

### How the system runs today

```bash
# Manual
python3 collector.py
python3 gold_signal.py              # or --mode relaxed
python3 resolve_trades.py           # auto-resolve PENDING trades
python3 analyze_trades.py           # performance report
python3 backtest.py                 # test rules on history

# Cron (recommended)
*/5 * * * * cd /opt/gold && /opt/gold/venv/bin/python collector.py >> collector.log 2>&1 && /opt/gold/venv/bin/python gold_signal.py >> signal.log 2>&1
```

Optional: run resolver every 30 min:
```cron
*/30 * * * * cd /opt/gold && /opt/gold/venv/bin/python resolve_trades.py >> resolve.log 2>&1
```

---

## Next Ideas (Priority Order)

### 1. Web Dashboard (high value)
Simple local web page that shows:
- Latest signal / current bias
- Recent trades + outcomes
- Win rate, average R, expectancy
- Quick status of collector / last candle time

Suggested stack: lightweight FastAPI or Flask + plain HTML (no heavy frontend needed).

### 2. Improve signal quality
Possible experiments (test with `backtest.py` first):
- Higher-timeframe trend filter (e.g. only take 5m signals in direction of 1H EMA)
- Session filter (London / NY only)
- Tighter/looser RSI or ATR filters
- Different SL/TP multiples

### 3. Better journal & analytics
- Equity curve export
- Breakdown by session / day of week
- Streak tracking (max wins / max losses in a row)

### 4. Optional later
- Paper-trading mode (simulate fills without real orders)
- Alert only when risk is under a threshold
- Multi-symbol support (if you expand beyond gold)

---

## If Something Breaks — Quick Recovery

### 1. No signals / empty DB
```bash
cd /opt/gold
source venv/bin/activate
python3 collector.py --backfill 500
python3 gold_signal.py
```

### 2. Telegram not sending
- Check `.env` has `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
- Confirm cron uses `cd /opt/gold` so `.env` is found

### 3. Journal in old format
```bash
mv trade_journal.csv trade_journal_old.csv
# next signal creates a clean journal with correct columns
```

### 4. Permission / git issues
```bash
git status
git pull
# if local runtime files conflict:
rm -f gold_data.db signal_state.json   # safe — they regenerate
git pull
python3 collector.py --backfill 500
```

### 5. Python / dependency issues
```bash
source venv/bin/activate
pip install -r requirements.txt
```

---

## Design Principles (don’t lose these)

1. **Manual decision aid first** — system suggests, you still place the trade.
2. **Same rules live & backtest** — never let them drift apart.
3. **Track everything** — every signal goes to the journal; resolve outcomes; review with `analyze_trades.py`.
4. **Secrets stay local** — `.env` is gitignored forever.
5. **Keep it simple** — prefer small scripts over a big framework until complexity is justified.

---

## Contact / Context

Repo: https://github.com/shashidaren/trading  
Server path: `/opt/gold`  
Main symbol: XAU/USD 5-minute

When continuing work later, start by reading this file.
