#!/usr/bin/env python3
"""
collector.py — pull XAU/USD 5-minute candles from Twelve Data and store them
in a local SQLite database. Safe to run repeatedly (upsert on timestamp).

Usage:
    # Ensure /opt/gold/.env contains: TWELVE_DATA_API_KEY="your_key_here"
    python3 collector.py                 # fetch latest bars (default 20), top up DB
    python3 collector.py --backfill 500  # pull up to 500 historical bars once
"""

import os
import sys
import sqlite3
import argparse
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

# Automatically load environment variables from /opt/gold/.env
load_dotenv()

API_URL = "https://api.twelvedata.com/time_series"
SYMBOL = "XAU/USD"
INTERVAL = "5min"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gold_data.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    ts      TEXT PRIMARY KEY,   -- ISO8601 UTC
    open    REAL NOT NULL,
    high    REAL NOT NULL,
    low     REAL NOT NULL,
    close   REAL NOT NULL,
    volume  REAL
);
"""


def get_conn():
    # timeout=5.0 prevents "database is locked" errors if signal.py reads simultaneously
    # WAL mode allows concurrent reads and writes
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(SCHEMA)
    return conn


def fetch_candles(api_key, outputsize=20):
    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": outputsize,
        "apikey": api_key,
        "order": "ASC",
        "timezone": "UTC",
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        print(f"[WARN] Network request failed: {e}")
        return []

    if data.get("status") == "error":
        print(f"[WARN] Twelve Data error: {data.get('message')}")
        return []

    values = data.get("values", [])
    return values


def upsert_candles(conn, values):
    if not values:
        return 0
        
    rows = []
    for v in values:
        # Twelve Data returns naive UTC timestamps like "2026-08-26 09:35:00"
        ts = v["datetime"].replace(" ", "T") + "Z"
        rows.append((
            ts,
            float(v["open"]),
            float(v["high"]),
            float(v["low"]),
            float(v["close"]),
            float(v.get("volume") or 0),
        ))

    conn.executemany(
        """INSERT INTO candles (ts, open, high, low, close, volume)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(ts) DO UPDATE SET
             open=excluded.open, high=excluded.high, low=excluded.low,
             close=excluded.close, volume=excluded.volume""",
        rows,
    )
    conn.commit()
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", type=int, default=None,
                         help="Pull this many historical 5m bars (max ~5000 depending on plan)")
    args = parser.parse_args()

    api_key = os.environ.get("TWELVE_DATA_API_KEY")
    if not api_key:
        sys.exit("ERROR: TWELVE_DATA_API_KEY not found. Please set it in /opt/gold/.env")

    # Default to 20 for cron (covers 100 mins of overlap), use backfill if specified
    outputsize = args.backfill if args.backfill else 20

    conn = get_conn()
    values = fetch_candles(api_key, outputsize=outputsize)
    
    if not values:
        print(f"[{datetime.now(timezone.utc).isoformat()}] No new data returned. Skipping.")
        sys.exit(0)

    n = upsert_candles(conn, values)

    latest = conn.execute("SELECT ts, close FROM candles ORDER BY ts DESC LIMIT 1").fetchone()
    print(f"[{datetime.now(timezone.utc).isoformat()}] upserted {n} candles. "
          f"Latest: {latest[0]}  close={latest[1]}")


if __name__ == "__main__":
    main()
