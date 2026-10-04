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
- Reads market-news RSS feeds such as Moneycontrol and LiveMint.
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

Default RSS sources:

- Moneycontrol market reports
- LiveMint markets

News is treated as context, not as the sole trade trigger.

The code stores:
- source
- headline
- URL
- published time
- simple sentiment
- market bias

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
