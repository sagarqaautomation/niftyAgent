# Nifty Intraday Market Agent

A VS Code-ready Python project for a **research/alert-only** NIFTY intraday monitoring system.

## What it does

- Connects to a broker market-data WebSocket through a replaceable adapter.
- Builds 1-minute and 5-minute candles.
- Calculates EMA9/EMA21, RSI14, VWAP, ATR14 and volume expansion.
- Combines technical conditions with option/news context.
- Produces CALL / PUT / WAIT research signals.
- Calculates entry, ATR-based stop-loss and risk/reward target.
- Tracks every signal in SQLite.
- Resolves signals against subsequent OHLC bars and records accuracy.
- Persists per-equity 1-minute/5-minute candles and signal-time feature snapshots for replay and model research.
- Exposes resolved equity outcomes with feature snapshots for offline training-data preparation; no AI model is trained or used yet.
- Reads multi-source market RSS headlines and tags matched equity/company context.
- Sends optional WhatsApp alerts through Twilio.
- Exposes REST endpoints for ChatGPT/other clients later.
- Keeps automatic order placement disabled.

> Important: This is an alert/research system, not a guaranteed prediction system and not an automatic trading bot. Always paper-test/backtest before using real money.

---

## 1. Requirements

Recommended:
- Windows 10/11
- Python 3.12+
- VS Code
- Git (optional)
- Zerodha Kite Connect or another licensed market-data provider
- Twilio WhatsApp Sandbox for testing

No second phone number is required for Twilio Sandbox testing.

---

## 2. Open in VS Code

Extract this ZIP, then:

```powershell
cd C:\Projects\nifty_intraday_agent_complete
code .
```

If `code` is not available, open the folder manually in VS Code.

---

## 3. Create Python environment

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## 4. Configure `.env`

Copy:

```text
.env.example
```

to:

```text
.env
```

Fill in your own credentials.

NEVER paste API secrets into ChatGPT or commit `.env` to Git.

---

## 5. Zerodha setup

Create a Kite Connect developer application and obtain:

- API key
- API secret
- Access token/session token as required by the authentication flow

Put them in `.env`.

The project deliberately does not hard-code credentials.

### Authentication note

Kite Connect uses a login/request-token flow. The exact daily authentication flow depends on your account/application setup. `broker/kite_adapter.py` contains the adapter boundary so authentication can be added without changing the signal engine.

For live market data, the project expects a broker WebSocket adapter.

---

## 6. Twilio WhatsApp testing

Configure WhatsApp using your Twilio Console account and an approved Content Template. Set `TWILIO_CONTENT_SID` to the template's Content SID. If it is empty, alerts are sent as free-form message bodies instead.

You do NOT need to buy another phone number for Sandbox testing.

Set:

```env
WHATSAPP_ENABLED=true
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_WHATSAPP_FROM=whatsapp:+<your-Twilio-WhatsApp-sender>
TWILIO_CONTENT_SID=HX...
WHATSAPP_TO=whatsapp:+91XXXXXXXXXX
```

For Sandbox testing, use the Sandbox sender and join it from your WhatsApp phone using the code shown in Twilio Console. For production, use your approved WhatsApp Business sender and approved template.

For production WhatsApp messaging, use the appropriate approved WhatsApp Business sender.

---

## 7. First run WITHOUT live broker

This verifies the project and SQLite database:

```powershell
python main.py
```

You should see:
- database initialized
- news fetch attempt
- current signal performance
- system status

---

## 8. Start REST API

```powershell
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Endpoints:

```text
GET /health
GET /performance
GET /latest-signal
GET /signals
GET /news
```

Example:

```text
http://127.0.0.1:8000/health
```

### MCP tools

The project also provides a read-only MCP server for MCP-compatible clients. It exposes market status, the latest and recent NIFTY signals, NIFTY and equity performance, recent equity signals, stored news headlines, and separate NIFTY spot and futures candle tools. Futures candles are volume context; spot candles are the index OHLC series. It does not connect to Kite or place orders; the market-data worker continues to own those responsibilities.

Install dependencies with `pip install -r requirements.txt`, then configure your MCP client to launch `mcp_server.py` over stdio. For example, in a VS Code MCP configuration, set `command` to the project's Python executable, `args` to the full path of `mcp_server.py`, and `cwd` to this project directory. Keep `cwd` set so `.env`, `schema.sql`, and `DB_PATH` resolve consistently. Start the app or API once first so the SQLite schema exists.

The MCP tools limit list results to 1-500 rows. They only read persisted project data and are intended for research and monitoring, not trading decisions.

---

## 9. Live broker architecture

The live flow is:

Broker WebSocket
  -> tick/candle engine
  -> indicators
  -> signal engine
  -> SQLite
  -> WhatsApp
  -> REST API

The core signal engine does NOT require an LLM and does NOT call an AI model every second.

On weekdays, the worker closes the Kite live-data WebSocket at 15:30 Asia/Kolkata and exits. If started after market close or on a weekend, it records the feed as `CLOSED` and does not connect. Start the worker again before the next trading session.

---

## 10. Signal logic

The default technical score uses:

### Bullish evidence
- 5m EMA9 > EMA21
- 1m close > VWAP
- RSI supports momentum
- volume expansion
- strong bullish candle

### Bearish evidence
- 5m EMA9 < EMA21
- 1m close < VWAP
- RSI supports downside momentum
- volume expansion
- strong bearish candle

The engine returns WAIT when evidence is weak or conflicting.

A signal is only considered eligible when the score passes the configured threshold.
After a score-qualified setup, the worker sends a CALL or PUT alert on the next closed-candle evaluation using the current NIFTY spot price. The UI, candles, entry, stop, target, and outcome tracking use NIFTY spot units. Futures data is used internally for volume confirmation only; these directional alerts do not select an option contract or strike.

### Equity scanner

The live worker can also scan the symbols in `data/equity_watchlist.csv`. The file has 50 symbols in each category: `NIFTY50`, `LARGE_CAP` (Nifty Next 50), `MID_CAP` (Nifty Midcap 50), and `SMALL_CAP` (Nifty Smallcap 50), sourced from the official [Nifty 50](https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv), [Nifty Next 50](https://www.niftyindices.com/IndexConstituent/ind_niftynext50list.csv), [Nifty Midcap 50](https://www.niftyindices.com/IndexConstituent/ind_niftymidcap50list.csv), and [Nifty Smallcap 50](https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap50list.csv) constituent files. Refresh the CSV after index reconstitutions. To use a custom list, keep one Kite `tradingsymbol` per row under the `symbol` column; the optional `category` column is for organization. The worker resolves only NSE cash-equity (`EQ`) instruments, subscribes to their live ticks, and loads minute history for indicator warm-up. Startup time increases with the number of symbols because history is fetched per stock.

Set `EQUITY_SCAN_ENABLED=false` to disable the scanner, or set `EQUITY_WATCHLIST_PATH` to a different CSV path. Equity candidates are BUY-only CALL setups that pass the configured score threshold. Each signal is stored separately in the `equity_signals` table and shown in the dashboard with entry, target, stop-loss, scores, reason, status, and an estimated holding time. That estimate extrapolates the target distance from the average absolute one-minute close change over the latest 20 closed bars; it is a rough historical measure, not a prediction or guarantee. Equity signals do not place orders and are not sent to WhatsApp by this scanner.

---

## 11. Risk model

Stop-loss uses ATR instead of a fixed number of points.

Example:

```text
Entry = 25,175
ATR = 25
SL distance = 1.2 * ATR = 30
CALL SL = 25,145
Target distance = 1.6R = 48
CALL Target = 25,223
```

These values are configurable and are NOT guaranteed profitable.

---

## 12. Accuracy tracking

Every signal gets:

- signal direction
- technical score
- context score
- total score
- entry
- target
- stop loss
- option symbol
- option entry
- status
- result price
- resolution time
- accuracy

Accuracy values:

```text
SUCCESS -> 100
FAILED -> 0
EXPIRED -> NULL
AMBIGUOUS -> NULL
```

If the same OHLC bar touches both target and stop, the system marks it AMBIGUOUS because OHLC alone cannot prove which happened first.

---

## 13. Option-chain integration

The project separates:

1. NIFTY spot/index feed
2. Option-chain discovery
3. Selected option quote
4. Signal engine

The broker adapter should supply the current NIFTY option contracts and their prices.

For production, implement the option selector so it chooses:
- nearest valid expiry
- configurable ATM / ITM / OTM distance
- sufficient liquidity
- acceptable spread

Do not assume an option premium target can be derived safely from NIFTY points without option pricing/quote data.

---

## 14. News

RSS sources:

- [Moneycontrol market reports](https://www.moneycontrol.com/rss/marketreports.xml)
- [LiveMint markets](https://www.livemint.com/rss/markets)
- [Economic Times markets](https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms)
- [Business Standard markets](https://www.business-standard.com/rss/markets-106.rss)

Headlines are tagged heuristically as earnings, company announcements, analyst actions, sector news, or market/macro news. The worker matches headlines against broker-provided company names and trading symbols, then applies fresh company-specific and market/macro context to equity signals. The NIFTY signal continues to use market-wide news context.

The code stores:
- source
- headline
- URL
- published time
- summary
- simple sentiment and market bias
- event categories
- matched equity symbols

The equity replay uses stored articles only at or after their recorded `fetched_at` time and within the configured freshness window. These feeds do not provide a complete six-month stock-specific news archive, so older candles without archived headlines remain neutral; live RSS headlines are never retroactively applied to earlier candles.

Respect each source's terms, robots rules, RSS/API conditions and copyright restrictions. Do not scrape/copy full articles.

---

## 15. Project structure

```text
nifty_intraday_agent_complete/
│
├── main.py
├── api.py
├── config.py
├── database.py
├── indicators.py
├── signal_engine.py
├── candle_engine.py
├── news_sources.py
├── whatsapp.py
├── requirements.txt
├── .env.example
├── .gitignore
├── schema.sql
├── Dockerfile
├── docker-compose.yml
│
├── broker/
│   ├── __init__.py
│   ├── base.py
│   └── kite_adapter.py
│
├── data/
│   └── .gitkeep
│
└── logs/
    └── .gitkeep
```

---

## 16. Recommended development sequence

### Phase 1
Run the project without live data.

### Phase 2
Connect Kite market-data WebSocket.

### Phase 3
Build and validate 1m/5m candles.

### Phase 4
Run signals in paper mode.

### Phase 5
Add option-chain selection and option-premium tracking.

### Phase 6
Measure signal accuracy for at least several weeks.

### Phase 7
Only after validation consider any broker order integration.

Automatic order placement is intentionally NOT included.

---

## 17. Docker

```powershell
docker compose up --build
```

The API will be available on:

```text
http://127.0.0.1:8000
```

---

## 18. Security

Never commit:

```text
.env
*.db
```

Never send:
- API secret
- access token
- Twilio auth token
- broker login credentials

to chat or GitHub.

---

## 19. Future ChatGPT integration

The REST API is intentionally simple so a future ChatGPT connector/agent can ask:

```text
What is the latest signal?
What is today's accuracy?
How many CALL signals succeeded?
Show today's news bias.
```

The ChatGPT layer should explain signals, not replace the deterministic market-data engine.


## v2.1 historical NIFTY data download

If you do not already have a 1-minute CSV, the project can download NIFTY 50 index candles directly through the same Kite Connect credentials used by the agent:

```powershell
python download_nifty_data.py --months 6
```

This creates:

```text
data/nifty_1m.csv
```

You can also specify an exact range:

```powershell
python download_nifty_data.py --start 2026-01-01 --end 2026-06-30
```

The downloader automatically splits requests into smaller chunks and throttles them. Kite's historical API supports minute candles and limits a single minute-data request to 60 calendar days, so the downloader does not request a larger window at once. The generated CSV is normalized to timestamp,open,high,low,close,volume, which is the format expected by backtester.py.

### Important volume limitation

NIFTY 50 itself is an index and does not have traded volume, so Kite returns zero volume for its historical index candles. That means the V2.1 relative-volume confirmation cannot be honestly validated from the NIFTY spot/index CSV alone. For a production-quality backtest, use a separate NIFTY futures volume series or run an explicitly price-only backtest; do not manufacture volume values.

The downloader prints a warning when it detects this condition.

### Recommended validation flow

```powershell
python download_nifty_data.py --months 6
python backtester.py data/nifty_1m.csv --walk-forward --folds 5
```

The first run validates the price/structure engine. Before using the result to claim V2.1 accuracy, add a historical futures-volume source so the volume gate is evaluated with real data.

### Equity signal-history download

To download six calendar months of 1-minute candles for the distinct stock symbols that generated equity signals today, run from the project directory:

    python download_equity_history.py --months 6

The downloader uses the existing Kite credentials, requests at most 30 calendar days per chunk, and writes one file per symbol under `data/equity_history_6m/` plus a `manifest.json`. Existing files are skipped unless `--overwrite` is supplied. Check the manifest for missing instruments, empty histories, or API failures before using the files for analysis.

Replay the live equity scanner across chronological validation folds with:

    python equity_backtester.py --data-dir data/equity_history_6m --scores 7 8 9 --folds 4 --progress

This writes `equity_backtest_summary.json` and `equity_backtest_trades.csv` in the data directory. Round-trip costs default to zero; supply a justified `--round-trip-cost-bps` value before interpreting net returns. The replay uses neutral historical news, enters at the next minute open, and excludes incomplete sessions and the first five warm-up dates.

## v2.1 accuracy engine

The v2.1 branch adds quality gates intended to reduce false positives before any live trading use:

- 5-minute trend slope and ADX regime detection.
- Relative-volume baseline that excludes the current candle.
- Prior-day high/low and completed 15-minute opening-range structure.
- Stronger range/high-volatility score requirements.
- Point-in-time signal feature snapshots for later model research.
- Time-aware news filtering so stale headlines do not drive intraday context.
- Setup reset + cooldown logic instead of suppressing every repeated signal forever.
- Risk-adjusted performance metrics: average R and profit factor.
- A look-ahead-safe historical backtester with ambiguous-bar handling and walk-forward reporting.

### Historical backtest

Prepare a CSV with:

    timestamp,open,high,low,close,volume
    2026-01-05 09:15:00+05:30,25000,25010,24990,25005,12345
    ...

Run from the project directory:

    python backtester.py data/nifty_1m.csv
    python backtester.py data/nifty_1m.csv --walk-forward --folds 5
    python backtester.py data/nifty_1m.csv --walk-forward --folds 5 --score-sweep 7 8 9 --round-trip-cost-points 2 --progress

The evaluator generates the signal only after a closed candle and enters at the next 1-minute candle open. If both target and stop are touched inside one OHLC candle, it records AMBIGUOUS instead of assuming which one was hit first.

The score sweep reports each fixed technical-score threshold separately on every validation fold. Round-trip costs are supplied in price points and default to zero; replace the example value with a realistic estimate for the instrument and execution method. Gross `average_r` and `profit_factor` remain available alongside `net_average_r` and `net_profit_factor`.

Do not optimize parameters on the same period used for the final performance claim. Use the walk-forward validation output to judge whether improvements survive unseen periods.

### Important live-data note

Kite historical candle timestamps represent the start of the candle, and Kite recommends building live candles from WebSocket data for live strategies. The v2.1 evaluator therefore treats the current live candle as mutable and only evaluates completed candles.

## Market-context and live validation (v2.1 accuracy engine)

The live signal pipeline now has separate inputs for domestic/global news, the
existing technical setup, directional candlestick confirmation, and the
observed breadth of the configured equity watchlist. It continues to emit
signals/alerts only; automatic order placement remains disabled by default.

### Global and domestic news
The RSS reader includes Indian market feeds plus CNBC World, CNBC Markets, and
BBC Business. Feeds are fetched concurrently so a slow feed does not serially
delay all other sources. Headlines with global macro terms are included in the
macro news aggregate. This is a lightweight keyword sentiment model, not a
financial-language model: verify headline polarity and source health before
using it for trading decisions.

### Constituent breadth and index weights
The agent calculates each configured equity's most recently closed 1-minute
return and derives a breadth bias only when at least 10 symbols are available
and at least 60% are advancing or declining. The live log records the number
of advancers, observed symbols, context bias, and weighting method.

For genuine index-weighted impact, provide a maintained
`data/nifty50_weights.csv` file with columns `symbol,weight_pct`. Use current
official NIFTY 50 constituent weights from an authoritative NSE source and
refresh them after index rebalances. If no file exists—or fewer than 60% of
the observed symbols have valid weights—the engine explicitly falls back to
equal-weighted breadth. It does not fabricate index weights. The configured
equity watchlist must contain the NIFTY 50 universe for this to represent full
NIFTY constituent breadth; a smaller custom watchlist is only a watchlist
breadth proxy.

### Candlestick scoring
A directional pattern on the latest completed candle contributes at most one
technical point, even if multiple overlapping patterns fire. Simultaneous
bullish and bearish patterns add no point and are reported as a conflict.
Pattern confirmation is one feature among trend, VWAP, momentum, volume and
structure—not a standalone entry rule.

### Live diagnostics
Each closed NIFTY candle prints a `[NIFTY ANALYSIS]` line with signal,
technical/context scores, regime, news bias, constituent breadth/method,
candlestick score/patterns, and reasons. Review these logs with the agent
running in `LIVE_MARKET_DATA=true` and the Kite session configured. This
repository connection cannot access a separately running local Kite session
or its private credentials, so it cannot verify your live broker ticks from
the GitHub repository alone.

### Validation cautions
GitHub Actions runs unit tests on push. A passing unit suite checks code
behavior; it does not establish a profitable strategy. Before enabling any
new gate, compare baseline versus news, breadth and candlestick variants using
the same point-in-time data, walk-forward splits, separate CALL/PUT metrics,
slippage/fees, and out-of-sample periods. Never use current constituent
weights or later-published headlines in historical rows where they were not
yet available.
