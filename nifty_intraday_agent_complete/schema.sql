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
    status TEXT NOT NULL DEFAULT 'OPEN'
      CHECK(status IN ('OPEN','SUCCESS','FAILED','EXPIRED','AMBIGUOUS')),
    result_price REAL,
    resolved_at TEXT,
    accuracy REAL,
    evaluation_minutes INTEGER
);

CREATE INDEX IF NOT EXISTS idx_candles_time ON candles(timeframe, timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at);
