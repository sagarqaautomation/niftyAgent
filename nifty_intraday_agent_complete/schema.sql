CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL DEFAULT 0,
    ema9 REAL,
    ema21 REAL,
    rsi14 REAL,
    vwap REAL,
    atr14 REAL,
    UNIQUE(timeframe, timestamp)
);

CREATE TABLE IF NOT EXISTS spot_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    UNIQUE(timeframe, timestamp)
);

CREATE TABLE IF NOT EXISTS equity_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL CHECK(timeframe IN ('1min','5min')),
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL DEFAULT 0,
    ema9 REAL,
    ema21 REAL,
    rsi14 REAL,
    vwap REAL,
    atr14 REAL,
    patterns_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(symbol, timeframe, timestamp)
);

CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    sentiment REAL,
    market_bias TEXT,
    UNIQUE(source, title)
);

CREATE TABLE IF NOT EXISTS market_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL,
    connected_at TEXT,
    last_tick_at TEXT,
    last_error TEXT,
    one_minute_bars INTEGER NOT NULL DEFAULT 0,
    five_minute_bars INTEGER NOT NULL DEFAULT 0,
    signal_instrument TEXT,
    last_exchange_tick_at TEXT,
    analysis_state TEXT NOT NULL DEFAULT 'WARMING_UP',
    spot_last_price REAL,
    spot_last_tick_at TEXT,
    analysis_score INTEGER,
    analysis_reason TEXT,
    analysis_updated_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    signal TEXT NOT NULL CHECK(signal IN ('CALL','PUT','WAIT')),
    technical_score INTEGER NOT NULL,
    context_score INTEGER NOT NULL,
    total_score INTEGER NOT NULL,
    entry_price REAL,
    target_price REAL,
    stop_loss REAL,
    option_symbol TEXT,
    option_entry REAL,
    reason TEXT,
    news_bias TEXT,
    option_bias TEXT,
    signal_instrument TEXT,
    signal_candle_time TEXT,
    spot_reference_price REAL,
    spot_reference_time TEXT,
    spot_trigger_price REAL,
    spot_trigger_offset REAL,
    spot_cross_price REAL,
    spot_cross_time TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN'
      CHECK(status IN ('OPEN','SUCCESS','FAILED','EXPIRED','AMBIGUOUS')),
    result_price REAL,
    resolved_at TEXT,
    accuracy REAL,
    evaluation_minutes INTEGER
);

CREATE TABLE IF NOT EXISTS equity_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        symbol TEXT NOT NULL,
        signal TEXT NOT NULL CHECK(signal='BUY'),
        technical_score INTEGER NOT NULL,
        context_score INTEGER NOT NULL,
        total_score INTEGER NOT NULL,
        entry_price REAL NOT NULL,
        target_price REAL NOT NULL,
        stop_loss REAL NOT NULL,
        expected_holding_minutes INTEGER,
        signal_candle_time TEXT NOT NULL,
        reason TEXT NOT NULL,
        feature_snapshot_json TEXT,
        status TEXT NOT NULL DEFAULT 'OPEN'
            CHECK(status IN ('OPEN','TARGET_HIT','STOP_HIT','AMBIGUOUS','EXPIRED')),
        result_price REAL,
        resolved_at TEXT,
        UNIQUE(symbol, signal_candle_time)
);

CREATE INDEX IF NOT EXISTS idx_candles_time ON candles(timeframe, timestamp);
CREATE INDEX IF NOT EXISTS idx_spot_candles_time ON spot_candles(timeframe, timestamp);
CREATE INDEX IF NOT EXISTS idx_equity_candles_symbol_time ON equity_candles(symbol, timeframe, timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at);
CREATE INDEX IF NOT EXISTS idx_equity_signals_created ON equity_signals(created_at);
CREATE INDEX IF NOT EXISTS idx_equity_signals_symbol_status ON equity_signals(symbol, status);
