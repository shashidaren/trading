def log_signal():
    # 1. Check if there is an active signal in the state file
    if not os.path.exists(STATE_FILE):
        print("No signal state found. Run gold_signal.py first.")
        return

    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
    except json.JSONDecodeError:
        print("Warning: signal_state.json is corrupted or empty. Creating fresh state.")
        state = {"last_signal_ts": None, "last_bias": None}
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
        return

    bias = state.get("last_bias")
    signal_ts = state.get("last_signal_ts")

    # If no active signal, or we already logged this exact timestamp, skip
    if not bias or bias == "NONE":
        print("No active BUY/SELL signal to log.")
        return
        
    if state.get("logged_ts") == signal_ts:
        print(f"Signal for {signal_ts} already logged. Skipping.")
        return

    # 2. Fetch the exact candle data from the DB for this timestamp
    conn = sqlite3.connect(DB_PATH)
    query = "SELECT ts, close, rsi, atr, vwap FROM candles WHERE ts = ?"
    # Note: state file stores ts as string, DB stores as ISO8601. 
    # We'll just grab the latest candle to be safe and accurate.
    df = pd.read_sql_query("SELECT * FROM candles ORDER BY ts DESC LIMIT 1", conn)
    conn.close()

    if df.empty:
        print("Error: Could not fetch candle data.")
        return

    last = df.iloc[0]
    entry = last['close']
    atr = last['atr']
    
    # Calculate SL/TP and Risk
    if bias == "BUY":
        sl = entry - SL_ATR_MULT * atr
        tp = entry + TP_ATR_MULT * atr
    else:
        sl = entry + SL_ATR_MULT * atr
        tp = entry - TP_ATR_MULT * atr
        
    sl_distance = abs(entry - sl)
    risk_usd = sl_distance * DOLLAR_PER_POINT
    risk_pct = (risk_usd / ACCOUNT_BALANCE) * 100

    # 3. Append to CSV
    file_exists = os.path.isfile(CSV_PATH)
    with open(CSV_PATH, mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            # Write headers if file is new
            writer.writerow([
                "Logged_At_UTC", "Signal_TS", "Bias", "Entry", "SL", "TP", 
                "ATR", "RSI", "Risk_USD", "Risk_Pct", "Outcome", "Notes"
            ])
        
        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            signal_ts,
            bias,
            f"{entry:.2f}",
            f"{sl:.2f}",
            f"{tp:.2f}",
            f"{atr:.2f}",
            f"{last['rsi']:.1f}",
            f"{risk_usd:.2f}",
            f"{risk_pct:.1f}%",
            "PENDING",  # You will manually update this to WIN/LOSS later
            ""           # Notes column for manual entry
        ])

    # 4. Update state file so we don't log it again
    state["logged_ts"] = signal_ts
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

    print(f"✅ Logged {bias} signal for {signal_ts} to trade_journal.csv")
