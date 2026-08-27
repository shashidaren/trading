# Gold (XAU/USD) 5m On-Demand Signal

Two scripts, meant to run on `bitcoin:/opt/gold`:

- `collector.py` — pulls 5-minute XAU/USD candles from Twelve Data into a
  local SQLite file `gold_data.db`. Safe to re-run; upserts by timestamp.
- `gold_signal.py` — reads the DB, computes EMA9/21, RSI14, ATR14, session VWAP,
  and prints a BUY / SELL / NONE read with ATR-based SL/TP for a quick
  manual entry decision.

## Setup

```bash
cd /opt/gold
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export TWELVE_DATA_API_KEY="your_key_here"   # or put it in your shell profile
```

## First run (backfill history)

```bash
python3 collector.py --backfill 500
python3 gold_signal.py
```

## On-demand use from then on

```bash
python3 collector.py   # top up latest bars
python3 gold_signal.py       # get current read
# or, looser trend-following rule instead of fresh EMA-cross only:
python3 gold_signal.py --mode relaxed
```

## Notes / things to tune

- **Rate limits**: Twelve Data's free tier is typically 8 requests/minute,
  800/day. `collector.py` does one request per run — fine for on-demand use,
  but don't cron it every few seconds.
- **Volume**: Twelve Data's spot XAU/USD feed sometimes reports 0/blank
  volume (it's an OTC-style spot instrument, not an exchange feed) — VWAP
  will fall back to `n/a` in that case and the confluence check just skips
  it. If you want real volume-based VWAP, point `SYMBOL` in `collector.py`
  at `GC=F`-style futures feeds instead.
- **SL/TP multiples** (`SL_ATR_MULT`, `TP_ATR_MULT` in `gold_signal.py`) are a
  starting point (1.5x / 2.5x ATR ≈ 1:1.67 R:R). Tune against your broker's
  typical spread on gold — if spread eats a big chunk of a 1.5×ATR stop on
  5m bars, widen it.
- **`strict` vs `relaxed` mode**: `strict` only fires right on a fresh
  EMA9/21 cross (fewer, more precise signals). `relaxed` fires anytime
  you're in-trend with RSI confirming (more signals, more noise) — better
  suited to "check in every 15–30 min and see if there's a trade" usage.
- This is a **manual-trade decision aid**, not an execution bot — nothing
  here places orders. You still pull the trigger in Moomoo/your broker.
