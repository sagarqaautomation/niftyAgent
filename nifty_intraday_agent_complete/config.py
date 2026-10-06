import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}

@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "nifty-intraday-agent")
    db_path: str = os.getenv("DB_PATH", "data/nifty_agent.db")
    underlying: str = os.getenv("UNDERLYING", "NIFTY")
    market_timezone: str = os.getenv("MARKET_TIMEZONE", "Asia/Kolkata")
    candle_timeframe: str = os.getenv("CANDLE_TIMEFRAME", "1min")
    confidence_threshold: int = int(os.getenv("CONFIDENCE_THRESHOLD", "7"))
    min_total_score: int = int(os.getenv("MIN_TOTAL_SCORE", "7"))
    atr_sl_multiplier: float = float(os.getenv("ATR_SL_MULTIPLIER", "1.2"))
    reward_risk: float = float(os.getenv("REWARD_RISK", "1.6"))
    signal_expiry_minutes: int = int(os.getenv("SIGNAL_EXPIRY_MINUTES", "30"))
    news_enabled: bool = env_bool("NEWS_ENABLED", True)
    news_refresh_seconds: int = int(os.getenv("NEWS_REFRESH_SECONDS", "120"))
    kite_api_key: str = os.getenv("KITE_API_KEY", "")
    kite_api_secret: str = os.getenv("KITE_API_SECRET", "")
    kite_access_token: str = os.getenv("KITE_ACCESS_TOKEN", "")
    nifty_instrument_token: str = os.getenv("NIFTY_INSTRUMENT_TOKEN", "")
    nifty_future_instrument_token: str = os.getenv("NIFTY_FUTURE_INSTRUMENT_TOKEN", "")
    kite_instruments_csv: str = os.getenv("KITE_INSTRUMENTS_CSV", "data/instruments.csv")
    equity_scan_enabled: bool = env_bool("EQUITY_SCAN_ENABLED", True)
    equity_watchlist_path: str = os.getenv(
        "EQUITY_WATCHLIST_PATH", "data/equity_watchlist.csv"
    )
    whatsapp_enabled: bool = env_bool("WHATSAPP_ENABLED", False)
    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_whatsapp_from: str = os.getenv("TWILIO_WHATSAPP_FROM", "")
    twilio_content_sid: str = os.getenv("TWILIO_CONTENT_SID", "")
    twilio_content_variables: str = os.getenv("TWILIO_CONTENT_VARIABLES", "")
    whatsapp_to: str = os.getenv("WHATSAPP_TO", "")
    live_market_data: bool = env_bool("LIVE_MARKET_DATA", False)
    paper_trading: bool = env_bool("PAPER_TRADING", True)
    auto_order_placement: bool = env_bool("AUTO_ORDER_PLACEMENT", False)

settings = Settings()
