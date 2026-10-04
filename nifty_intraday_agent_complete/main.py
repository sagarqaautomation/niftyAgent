import time
import pandas as pd

from config import settings
from database import (
    init_db, insert_news, insert_candle, insert_signal,
    performance, latest_signal, resolve_with_bar, expire_signals
)
from news_sources import fetch_all, aggregate_news
from candle_engine import CandleEngine
from signal_engine import build_signal, add_risk_levels
from whatsapp import send_whatsapp, format_signal

def refresh_news():
    if not settings.news_enabled:
        return "NEUTRAL"
    items = fetch_all()
    for item in items:
        insert_news(item)
    return aggregate_news(items)

def process_frames(frames, news_bias="NEUTRAL", option_bias="NEUTRAL"):
    df1 = frames["1min"]
    df5 = frames["5min"]
    if df1.empty or df5.empty:
        return None

    row = df1.iloc[-1]
    insert_candle({
        "timeframe": "1min",
        "timestamp": str(df1.index[-1]),
        "open": row["open"], "high": row["high"], "low": row["low"],
        "close": row["close"], "volume": row["volume"],
        "ema9": row["ema9"], "ema21": row["ema21"],
        "rsi14": row["rsi14"], "vwap": row["vwap"], "atr14": row["atr14"]
    })

    signal = build_signal(df1, df5, news_bias, option_bias)
    signal = add_risk_levels(signal)

    if signal["signal"] != "WAIT":
        sid = insert_signal(signal)
        signal["id"] = sid
        send_whatsapp(format_signal(signal))

    resolve_with_bar({
        "open": row["open"], "high": row["high"],
        "low": row["low"], "close": row["close"]
    })
    expire_signals()
    return signal

def main():
    init_db()
    print("Nifty Intraday Agent initialized.")
    print("Mode:", "LIVE DATA" if settings.live_market_data else "DEMO/NO LIVE DATA")
    print("Paper trading:", settings.paper_trading)
    print("Auto order placement:", settings.auto_order_placement)

    if settings.news_enabled:
        bias = refresh_news()
        print("Current news bias:", bias)

    print("Performance:", performance())

    if not settings.live_market_data:
        print("\nProject is ready. Set LIVE_MARKET_DATA=true after configuring the broker adapter.")
        return

    from broker.kite_adapter import KiteMarketData

    engine = CandleEngine()
    broker = KiteMarketData().connect()

    news_bias = "NEUTRAL"

    def on_ticks(ticks):
        nonlocal news_bias
        for tick in ticks:
            ts = tick.get("exchange_timestamp") or tick.get("timestamp")
            price = tick.get("last_price")
            volume = tick.get("volume_traded", 0)
            if ts is None or price is None:
                continue

            frames = engine.add_tick(ts, price, volume)
            if frames:
                result = process_frames(frames, news_bias, "NEUTRAL")
                if result and result["signal"] != "WAIT":
                    print("SIGNAL:", result)

    broker.on_tick(on_ticks)
    broker.start()

    while True:
        time.sleep(settings.news_refresh_seconds)
        try:
            news_bias = refresh_news()
            print("News bias refreshed:", news_bias)
        except Exception as exc:
            print("News refresh error:", exc)

if __name__ == "__main__":
    main()
