# Gold (XAU/USD) 5m Signal System

Simple on-demand signal helper for gold.  
Gives you BUY / SELL / NONE reads with ATR-based SL/TP, sends Telegram alerts, and logs every signal so you can track accuracy over time.

## Files

| File | Purpose |
|------|---------|
| `collector.py` | Pulls 5-minute XAU/USD candles from Twelve Data → `gold_data.db` |
| `gold_signal.py` | Calculates indicators, generates signal, Telegram + journal logging |
| `analyze_trades.py` | Performance report from `trade_journal.csv` |
| `trade_journal.csv` | Auto-created log of every signal (you mark WIN/LOSS later) |
| `.env` | Your secrets (never commit this) |

## Setup

```bash
cd /opt/gold          # or wherever you cloned the repo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:

```env
TWELVE_DATA_API_KEY=your_key_here
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

## Daily usage

```bash
# 1. Update candles
python3 collector.py

# 2. Get signal (strict = fresh EMA cross only)
python3 gold_signal.py

# or more signals (in-trend + RSI)
python3 gold_signal.py --mode relaxed
```

## Tracking accuracy

Every time a real signal fires it is appended to `trade_journal.csv` with `Outcome = PENDING`.

After the trade finishes:

1. Open `trade_journal.csv`
2. Change `Outcome` to `WIN`, `LOSS` or `BE`
3. (Optional) fill `R_Multiple` (e.g. `1.67` if you hit full 2.5 ATR target against 1.5 ATR risk)
4. Run:

```bash
python3 analyze_trades.py
```

You will see win rate, average R, expectancy, breakdown by mode and direction.

## Notes

- **Rate limits**: Twelve Data free tier is limited. `collector.py` does one request per run — fine for on-demand use.
- **Volume / VWAP**: Spot XAU/USD often has blank volume. VWAP confirmation is skipped when volume is missing.
- **Risk numbers**: Adjust `ACCOUNT_BALANCE`, `LOT_SIZE` and `DOLLAR_PER_POINT` at the top of `gold_signal.py` to match your broker.
- **Cooldown**: Default 15 minutes between signals to reduce whipsaw spam.

## First-time backfill

```bash
python3 collector.py --backfill 500
python3 gold_signal.py
```
