import time
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime, time as datetime_time, timedelta, timezone
from typing import Any, cast
import pandas as pd
from zoneinfo import ZoneInfo

from config import settings
from database import (
    init_db, insert_news, insert_candles, insert_spot_candles, insert_signal,
    performance, resolve_with_bar, expire_signals, update_market_status,
    update_spot_status, insert_equity_signal, insert_equity_candles,
    resolve_equity_signal,
    expire_stale_equity_signals,
    update_analysis_status, utc_now
)
from news_sources import fetch_all, aggregate_news
from candle_engine import CandleEngine
from signal_engine import build_signal, add_risk_levels
from equity_scanner import (
    build_equity_buy_signal, load_watchlist,
    resolve_equity_tokens,
)
from whatsapp import send_whatsapp, format_signal

Signal = dict[str, Any]
NewsItem = dict[str, Any]
CandleFrames = dict[str, pd.DataFrame]
CandleRecord = dict[str, Any]
Tick = Mapping[str, Any]

def market_session_has_ended(now: datetime | None = None) -> bool:
    market_now = now or datetime.now(ZoneInfo(settings.market_timezone))
    if market_now.tzinfo is None:
        market_now = market_now.replace(tzinfo=ZoneInfo(settings.market_timezone))
    else:
        market_now = market_now.astimezone(ZoneInfo(settings.market_timezone))
    return market_now.weekday() >= 5 or market_now.time() >= datetime_time(15, 30)


def refresh_news() -> str:
    if not settings.news_enabled:
        return "NEUTRAL"
    items: list[NewsItem] = cast(list[NewsItem], fetch_all())
    for item in items:
        insert_news(item)
    return cast(str, aggregate_news(items))

def candle_record(
    timeframe: str,
    timestamp: pd.Timestamp | datetime | str,
    row: pd.Series[Any],
) -> CandleRecord:
    candle_time = pd.Timestamp(timestamp)
    if candle_time.tzinfo is not None:
        candle_time = candle_time.tz_convert(settings.market_timezone).tz_localize(None)
    return {
        "timeframe": timeframe,
        "timestamp": str(candle_time),
        "open": row["open"], "high": row["high"], "low": row["low"],
        "close": row["close"], "volume": row["volume"],
        "ema9": row["ema9"], "ema21": row["ema21"],
        "rsi14": row["rsi14"], "vwap": row["vwap"], "atr14": row["atr14"]
    }

def spot_candle_record(
    timeframe: str,
    timestamp: pd.Timestamp | datetime | str,
    row: pd.Series[Any],
) -> CandleRecord:
    candle_time = pd.Timestamp(timestamp)
    if candle_time.tzinfo is not None:
        candle_time = candle_time.tz_convert(settings.market_timezone).tz_localize(None)
    return {
        "timeframe": timeframe,
        "timestamp": str(candle_time),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
    }

def equity_candle_record(
    symbol: str,
    timeframe: str,
    timestamp: pd.Timestamp | datetime | str,
    row: pd.Series[Any],
) -> CandleRecord:
    return {
        "symbol": symbol,
        **candle_record(timeframe, timestamp, row),
    }

def closed_bar_counts(
    frames: CandleFrames,
    current_minute: pd.Timestamp | None = None,
) -> tuple[int, int]:
    if current_minute is None:
        current_minute = pd.Timestamp.now(tz=settings.market_timezone).floor("min")
    current_five_bucket = current_minute.floor("5min")
    one_minute_bars = int((frames["1min"].index < current_minute).sum())
    five_minute_bars = int((frames["5min"].index < current_five_bucket).sum())
    return one_minute_bars, five_minute_bars

def spot_quote_age_seconds(spot_time: str | pd.Timestamp) -> float:
    timestamp = pd.Timestamp(spot_time)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(settings.market_timezone)
    else:
        timestamp = timestamp.tz_convert(settings.market_timezone)
    now = pd.Timestamp.now(tz=settings.market_timezone)
    return max(0.0, (now - timestamp).total_seconds())

def make_spot_signal(
    signal: Signal,
    spot_price: float,
    spot_time: str,
    spot_atr: float,
) -> Signal:
    spot_signal = signal.copy()
    spot_signal["signal_instrument"] = "NIFTY spot index"
    spot_signal["signal_candle_time"] = spot_time
    spot_signal["spot_reference_price"] = float(spot_price)
    spot_signal["spot_reference_time"] = spot_time
    spot_signal["entry_price"] = float(spot_price)
    spot_signal["atr"] = float(spot_atr)
    return add_risk_levels(spot_signal)

def process_frames(
    frames: CandleFrames,
    news_bias: str = "NEUTRAL",
    option_bias: str = "NEUTRAL",
    signal_instrument: str | None = None,
    spot_reference: Mapping[str, str | float] | None = None,
) -> Signal | None:
    df1 = frames["1min"]
    df5 = frames["5min"]
    if df1.empty or df5.empty:
        return None

    row = df1.iloc[-1]
    insert_candles([
        candle_record("1min", df1.index[-1], row),
        candle_record("5min", df5.index[-1], df5.iloc[-1]),
    ])

    signal: Signal = build_signal(df1, df5, news_bias, option_bias)
    signal["signal_instrument"] = signal_instrument or "NIFTY spot index"
    signal["signal_candle_time"] = (
        spot_reference["timestamp"] if spot_reference else df1.index[-1].isoformat()
    )
    signal["spot_reference_price"] = (
        spot_reference["price"] if spot_reference else None
    )
    signal["spot_reference_time"] = (
        spot_reference["timestamp"] if spot_reference else None
    )
    update_analysis_status(
        signal.get("analysis_state", signal["signal"]),
        signal["technical_score"],
        signal["reason"],
    )

    return signal

def main() -> None:
    init_db()
    update_market_status(
        "STARTING", last_error="", one_minute_bars=0, five_minute_bars=0,
        analysis_state="WARMING_UP", analysis_score=0,
        analysis_reason="Starting market-data worker",
        analysis_updated_at=utc_now(),
    )
    print("Nifty Intraday Agent initialized.")
    print("Mode:", "LIVE DATA" if settings.live_market_data else "DEMO/NO LIVE DATA")
    print("Paper trading:", settings.paper_trading)
    print("Auto order placement:", settings.auto_order_placement)

    if not settings.live_market_data:
        if settings.news_enabled:
            bias = refresh_news()
            print("Current news bias:", bias)
        print("Performance:", performance())
        print("\nProject is ready. Set LIVE_MARKET_DATA=true after configuring the broker adapter.")
        return

    if market_session_has_ended():
        expire_stale_equity_signals(expire_all_open=True)
        closed_reason = "NSE session is closed; live market data was not started."
        update_market_status(
            "CLOSED",
            last_error="",
            analysis_state="CLOSED",
            analysis_reason=closed_reason,
            analysis_updated_at=utc_now(),
        )
        print(closed_reason, flush=True)
        return

    if settings.news_enabled:
        bias = refresh_news()
        print("Current news bias:", bias)

    print("Performance:", performance())

    from broker.kite_adapter import KiteMarketData

    engine: Any = CandleEngine()
    spot_engine: Any = CandleEngine()
    try:
        broker: Any = KiteMarketData().connect()
    except Exception as exc:
        update_market_status(
            "ERROR", last_error=str(exc), analysis_state="ERROR",
            analysis_reason=f"Broker connection failed: {exc}",
            analysis_updated_at=utc_now(),
        )
        raise

    try:
        if settings.nifty_future_instrument_token:
            instrument_token = int(settings.nifty_future_instrument_token)
        else:
            future: dict[str, Any] = broker.get_front_future(settings.underlying)
            instrument_token = int(future["instrument_token"])
        spot_token = int(settings.nifty_instrument_token) if settings.nifty_instrument_token else None
        equity_tokens_by_symbol: dict[str, int] = {}
        if settings.equity_scan_enabled:
            try:
                equity_symbols = load_watchlist(settings.equity_watchlist_path)
                equity_instruments = broker.get_instruments("NSE")
                equity_tokens_by_symbol, missing_equities = resolve_equity_tokens(
                    equity_instruments, equity_symbols
                )
                if missing_equities:
                    try:
                        bse_instruments = broker.get_instruments("BSE")
                        bse_tokens, missing_equities = resolve_equity_tokens(
                            bse_instruments, missing_equities, exchange="BSE"
                        )
                        equity_tokens_by_symbol.update(bse_tokens)
                    except Exception as exc:
                        print(f"BSE equity fallback failed: {exc}")
                print(
                    f"Equity watchlist: {len(equity_tokens_by_symbol)}/{len(equity_symbols)} "
                    "NSE symbols resolved"
                )
                if missing_equities:
                    print("Unmatched equity symbols:", ", ".join(missing_equities))
            except Exception as exc:
                print(f"Equity scan setup failed; continuing with NIFTY only: {exc}")
                equity_tokens_by_symbol = {}
        equity_symbol_by_token = {
            token: symbol for symbol, token in equity_tokens_by_symbol.items()
        }
        subscribed_tokens = [instrument_token]
        if spot_token is not None and spot_token != instrument_token:
            subscribed_tokens.append(spot_token)
        subscribed_tokens.extend(equity_tokens_by_symbol.values())
        broker.set_instrument_tokens(subscribed_tokens)
        update_market_status(
            "STARTING", signal_instrument="NIFTY spot index",
            analysis_state="WARMING_UP",
            analysis_reason="Loading futures minute history",
            analysis_updated_at=utc_now(),
        )
    except Exception as exc:
        update_market_status(
            "ERROR", last_error=str(exc), analysis_state="ERROR",
            analysis_reason=f"Could not select NIFTY futures instrument: {exc}",
            analysis_updated_at=utc_now(),
        )
        raise

    equity_engines: dict[str, Any] = {
        symbol: CandleEngine() for symbol in equity_tokens_by_symbol
    }
    equity_engine_locks = {
        symbol: threading.RLock() for symbol in equity_tokens_by_symbol
    }
    last_equity_processed: dict[str, pd.Timestamp] = {}
    last_equity_persisted_five_minute: dict[str, pd.Timestamp] = {}
    active_equity_setups: set[str] = set()
    equity_cumulative_volumes: dict[str, float] = {}
    equity_volume_dates: dict[str, Any] = {}

    try:
        history_end = pd.Timestamp.now(tz=settings.market_timezone).to_pydatetime()
        history_start = history_end - timedelta(days=5)
        history: list[dict[str, Any]] = broker.historical_data(
            instrument_token, history_start, history_end, "minute"
        )
        if not history:
            raise RuntimeError("Kite returned no NIFTY futures minute history")
        if not any(float(bar.get("volume") or 0) > 0 for bar in history):
            raise RuntimeError("NIFTY futures history contains no traded volume")
        frames: CandleFrames = engine.load_history(history)
        historical_rows: list[CandleRecord] = []
        for timeframe, frame in frames.items():
            historical_rows.extend(
                candle_record(timeframe, cast(pd.Timestamp, timestamp), row)
                for timestamp, row in frame.iterrows()
            )
        insert_candles(historical_rows)
        one_minute_bars, five_minute_bars = closed_bar_counts(frames)
        update_market_status(
            "STARTING",
            last_error="",
            one_minute_bars=one_minute_bars,
            five_minute_bars=five_minute_bars,
            analysis_state="WARMING_UP",
            analysis_score=0,
            analysis_reason=(
                f"Loaded futures history; {one_minute_bars} closed 1m and "
                f"{five_minute_bars} closed 5m bars. Waiting for live evaluation."
            ),
            analysis_updated_at=utc_now(),
        )
        print(
            "Historical warm-up loaded:",
            len(frames["1min"]), "1m bars,",
            len(frames["5min"]), "5m bars"
        )
        if spot_token is not None:
            try:
                spot_history: list[dict[str, Any]] = broker.historical_data(
                    spot_token, history_start, history_end, "minute"
                )
                spot_frames: CandleFrames = spot_engine.load_history(spot_history)
                spot_rows: list[CandleRecord] = [
                    spot_candle_record(timeframe, cast(pd.Timestamp, timestamp), row)
                    for timeframe, frame in spot_frames.items()
                    for timestamp, row in frame.iterrows()
                ]
                insert_spot_candles(spot_rows)
                print("NIFTY spot history loaded:", len(spot_frames["1min"]), "1m bars")
            except Exception as exc:
                print("NIFTY spot history warm-up error:", exc)
    except Exception as exc:
        update_market_status(
            "STARTING",
            last_error=f"Historical warm-up failed: {exc}",
            one_minute_bars=0,
            five_minute_bars=0,
            analysis_state="BLOCKED",
            analysis_score=0,
            analysis_reason=f"Historical futures warm-up failed: {exc}",
            analysis_updated_at=utc_now(),
        )
        print("Historical warm-up error:", exc)

    news_bias = "NEUTRAL"
    last_status_write = 0.0
    last_spot_status_write = 0.0
    last_processed_minute = None
    last_cumulative_volume = None
    last_volume_date = None
    latest_spot_price = None
    latest_spot_time = None
    latest_spot_atr = None
    last_resolved_spot_minute = None
    latest_future_price = None
    latest_future_time = None
    triggered_direction = None

    def on_status(state: str, detail: str | None = None) -> None:
        update_market_status(
            state,
            connected_at=utc_now() if state == "CONNECTED" else None,
            last_error=detail if state in {"ERROR", "DISCONNECTED"} else None,
        )

    spot_lock = threading.RLock()

    def process_spot_quote(spot_price: float, spot_time: pd.Timestamp) -> None:
        nonlocal latest_spot_price, latest_spot_time, latest_spot_atr
        nonlocal last_spot_status_write, last_resolved_spot_minute
        if spot_time.tzinfo is None:
            spot_time = spot_time.tz_localize(settings.market_timezone)
        else:
            spot_time = spot_time.tz_convert(settings.market_timezone)

        with spot_lock:
            latest_spot_price = float(spot_price)
            latest_spot_time = spot_time.isoformat()
            try:
                spot_frames = spot_engine.add_tick(spot_time, latest_spot_price, 0)
            except Exception as exc:
                update_analysis_status(
                    "ERROR", None,
                    f"Could not build NIFTY spot candle: {type(exc).__name__}: {exc}",
                )
                return

            if spot_frames:
                spot_rows = [
                    spot_candle_record("1min", spot_frames["1min"].index[-1], spot_frames["1min"].iloc[-1]),
                    spot_candle_record("5min", spot_frames["5min"].index[-1], spot_frames["5min"].iloc[-1]),
                ]
                insert_spot_candles(spot_rows)
                current_spot_atr = spot_frames["1min"].iloc[-1]["atr14"]
                if pd.notna(current_spot_atr):
                    latest_spot_atr = float(current_spot_atr)

                closed_spot = spot_frames["1min"].iloc[:-1]
                if not closed_spot.empty:
                    spot_bar_time = closed_spot.index[-1]
                    if spot_bar_time != last_resolved_spot_minute:
                        last_resolved_spot_minute = spot_bar_time
                        spot_bar = closed_spot.iloc[-1]
                        resolve_with_bar({
                            "open": spot_bar["open"],
                            "high": spot_bar["high"],
                            "low": spot_bar["low"],
                            "close": spot_bar["close"],
                        })
                        expire_signals()

            now = time.monotonic()
            if now - last_spot_status_write >= 1:
                update_spot_status(latest_spot_price, latest_spot_time)
                last_spot_status_write = now

    def on_ticks(ticks: Sequence[Tick]) -> None:
        nonlocal news_bias, last_status_write, last_processed_minute
        nonlocal last_spot_status_write
        nonlocal last_cumulative_volume, last_volume_date
        nonlocal latest_spot_price, latest_spot_time
        nonlocal latest_future_price, latest_future_time
        nonlocal latest_spot_atr, last_resolved_spot_minute
        nonlocal triggered_direction
        for tick in ticks:
            tick_token = tick.get("instrument_token")
            equity_symbol = (
                equity_symbol_by_token.get(int(tick_token))
                if tick_token is not None else None
            )
            if spot_token is not None and tick_token is not None and int(tick_token) == spot_token:
                spot_price = tick.get("last_price")
                if spot_price is not None:
                    spot_timestamp = (
                        tick.get("exchange_timestamp")
                        or tick.get("timestamp")
                        or pd.Timestamp.now(tz=settings.market_timezone)
                    )
                    process_spot_quote(float(spot_price), pd.Timestamp(spot_timestamp))

                continue
            if equity_symbol is not None:
                equity_price = tick.get("last_price")
                if equity_price is None:
                    continue
                equity_timestamp = (
                    tick.get("exchange_timestamp")
                    or tick.get("timestamp")
                    or pd.Timestamp.now(tz=settings.market_timezone)
                )
                equity_time = pd.Timestamp(equity_timestamp)
                if equity_time.tzinfo is None:
                    equity_time = equity_time.tz_localize(settings.market_timezone)
                else:
                    equity_time = equity_time.tz_convert(settings.market_timezone)

                cumulative_volume = float(tick.get("volume_traded") or 0)
                tick_date = equity_time.date()
                previous_volume = equity_cumulative_volumes.get(equity_symbol)
                if equity_volume_dates.get(equity_symbol) != tick_date or previous_volume is None:
                    tick_volume = 0.0
                elif cumulative_volume < previous_volume:
                    tick_volume = cumulative_volume
                else:
                    tick_volume = cumulative_volume - previous_volume
                equity_cumulative_volumes[equity_symbol] = cumulative_volume
                equity_volume_dates[equity_symbol] = tick_date

                equity_engine = equity_engines.get(equity_symbol)
                if equity_engine is None:
                    continue
                try:
                    with equity_engine_locks[equity_symbol]:
                        equity_frames: CandleFrames = equity_engine.add_tick(
                            equity_time, float(equity_price), tick_volume
                        )
                except Exception as exc:
                    print(f"Could not build {equity_symbol} candles: {exc}")
                    continue
                if not equity_frames:
                    continue

                equity_minute_frame: pd.DataFrame = equity_frames["1min"]
                equity_current_minute = cast(
                    pd.Timestamp, equity_minute_frame.index[-1]
                )
                closed_equity_minutes: pd.DataFrame = equity_minute_frame.loc[
                    equity_minute_frame.index < equity_current_minute
                ]
                if closed_equity_minutes.empty:
                    continue
                closed_equity_time = cast(pd.Timestamp, closed_equity_minutes.index[-1])
                if last_equity_processed.get(equity_symbol) == closed_equity_time:
                    continue
                last_equity_processed[equity_symbol] = closed_equity_time
                equity_bar: pd.Series[Any] = closed_equity_minutes.iloc[-1]
                equity_candle_rows = [
                    equity_candle_record(
                        equity_symbol,
                        "1min",
                        closed_equity_time,
                        equity_bar,
                    )
                ]
                current_five_bucket = equity_current_minute.floor("5min")
                closed_equity_five_minutes: pd.DataFrame = equity_frames["5min"].loc[
                    equity_frames["5min"].index < current_five_bucket
                ]
                if not closed_equity_five_minutes.empty:
                    closed_five_time = cast(
                        pd.Timestamp, closed_equity_five_minutes.index[-1]
                    )
                    if last_equity_persisted_five_minute.get(equity_symbol) != closed_five_time:
                        equity_candle_rows.append(equity_candle_record(
                            equity_symbol,
                            "5min",
                            closed_five_time,
                            closed_equity_five_minutes.iloc[-1],
                        ))
                        last_equity_persisted_five_minute[equity_symbol] = closed_five_time
                insert_equity_candles(equity_candle_rows)
                resolve_equity_signal(equity_symbol, {
                    key: float(cast(float, equity_bar[key]))
                    for key in ("open", "high", "low", "close")
                }, closed_equity_time.to_pydatetime())

                equity_signal = build_equity_buy_signal(
                    equity_symbol,
                    equity_frames,
                    float(equity_price),
                    news_bias,
                )
                if equity_signal is None:
                    active_equity_setups.discard(equity_symbol)
                    continue
                if spot_quote_age_seconds(equity_time) > 10:
                    print(f"Skipping stale {equity_symbol} quote")
                    continue
                if equity_symbol in active_equity_setups:
                    continue
                signal_id = insert_equity_signal(equity_signal)
                active_equity_setups.add(equity_symbol)
                if signal_id is not None:
                    equity_signal["id"] = signal_id
                    print("EQUITY BUY SIGNAL:", equity_signal, flush=True)
                continue

            if tick_token is not None and int(tick_token) != instrument_token:
                continue
            ts = (
                tick.get("exchange_timestamp")
                or tick.get("timestamp")
                or pd.Timestamp.now(tz=settings.market_timezone)
            )
            price = tick.get("last_price")
            if price is None:
                continue

            tick_time = pd.Timestamp(ts)
            if tick_time.tzinfo is None:
                tick_time = tick_time.tz_localize(settings.market_timezone)
            else:
                tick_time = tick_time.tz_convert(settings.market_timezone)
            latest_future_price = float(price)
            latest_future_time = tick_time

            cumulative_volume = float(tick.get("volume_traded") or 0)
            tick_date = tick_time.date()
            if last_volume_date != tick_date or last_cumulative_volume is None:
                tick_volume = 0
            elif cumulative_volume < last_cumulative_volume:
                tick_volume = cumulative_volume
            else:
                tick_volume = cumulative_volume - last_cumulative_volume
            last_cumulative_volume = cumulative_volume
            last_volume_date = tick_date

            try:
                frames = engine.add_tick(tick_time, price, tick_volume)
            except Exception as exc:
                update_analysis_status(
                    "ERROR", None,
                    f"Could not build futures candle: {type(exc).__name__}: {exc}",
                )
                continue

            if frames:
                current_minute = frames["1min"].index[-1]
                current_rows = [
                    candle_record("1min", current_minute, frames["1min"].iloc[-1]),
                    candle_record("5min", frames["5min"].index[-1], frames["5min"].iloc[-1]),
                ]
                insert_candles(current_rows)

                now = time.monotonic()
                if now - last_status_write >= 1:
                    one_minute_bars, five_minute_bars = closed_bar_counts(
                        frames, current_minute
                    )
                    update_market_status(
                        "LIVE",
                        last_tick_at=utc_now(),
                        last_exchange_tick_at=tick_time.isoformat(),
                        last_error="",
                        one_minute_bars=one_minute_bars,
                        five_minute_bars=five_minute_bars,
                    )
                    last_status_write = now

                closed_minute = frames["1min"].iloc[:-1]
                closed_five = frames["5min"]
                current_five_bucket = current_minute.floor("5min")
                closed_five = closed_five.loc[closed_five.index < current_five_bucket]
                if not closed_minute.empty:
                    closed_timestamp = closed_minute.index[-1]
                    if closed_timestamp != last_processed_minute:
                        last_processed_minute = closed_timestamp
                        try:
                            result = process_frames(
                                {"1min": closed_minute, "5min": closed_five},
                                news_bias,
                                "NEUTRAL",
                                "NIFTY spot index",
                                {
                                    "price": latest_spot_price,
                                    "timestamp": latest_spot_time,
                                }
                                if latest_spot_price is not None and latest_spot_time is not None
                                else None,
                            )
                        except Exception as exc:
                            update_analysis_status(
                                "ERROR", None,
                                f"Signal evaluation failed: {type(exc).__name__}: {exc}",
                            )
                            continue
                        if not result:
                            continue
                        direction = result["signal"]
                        if direction == "WAIT":
                            triggered_direction = None
                            continue

                        if triggered_direction == direction:
                            update_analysis_status(
                                f"{direction}_TRIGGERED", result["technical_score"],
                                f"{direction} alert already sent for this setup; waiting for setup reset.",
                            )
                            continue

                        if latest_spot_price is None or latest_spot_time is None:
                            update_analysis_status(
                                "BLOCKED", None,
                                "Score-qualified setup found, but no current NIFTY spot quote is available.",
                            )
                            continue
                        spot_age = spot_quote_age_seconds(latest_spot_time)
                        if spot_age > 10:
                            update_analysis_status(
                                "BLOCKED", None,
                                f"Score-qualified setup found, but NIFTY spot quote is stale ({spot_age:.0f}s).",
                            )
                            continue

                        future_age = (
                            datetime.now(timezone.utc)
                            - latest_future_time.astimezone(timezone.utc)
                        ).total_seconds() if latest_future_time else float("inf")
                        if latest_spot_atr is None or future_age > 10:
                            update_analysis_status(
                                "BLOCKED", None,
                                "Score-qualified setup found, but spot ATR or volume confirmation is stale.",
                            )
                            continue

                        signal = make_spot_signal(
                            result,
                            latest_spot_price,
                            latest_spot_time,
                            latest_spot_atr,
                        )
                        signal["reason"] = f"{signal['reason']}; sent at current NIFTY spot"
                        signal["id"] = insert_signal(signal)
                        delivery: dict[str, str | bool]
                        try:
                            delivery = send_whatsapp(format_signal(signal), signal)
                        except Exception as exc:
                            delivery = {
                                "sent": False,
                                "reason": f"{type(exc).__name__}: {exc}",
                            }
                        if not delivery.get("sent"):
                            delivery_reason = delivery.get("reason") or "Unknown delivery error"
                            print("WhatsApp alert not sent:", delivery_reason, flush=True)
                            update_analysis_status(
                                f"{direction}_ALERT_NOT_SENT",
                                signal["technical_score"],
                                f"{direction} signal saved as #{signal['id']}, but WhatsApp delivery failed: {delivery_reason}",
                            )
                        else:
                            update_analysis_status(
                                direction, signal["technical_score"], signal["reason"]
                            )
                        triggered_direction = direction
                        print("SIGNAL:", signal)

    broker.on_status(on_status)
    broker.on_tick(on_ticks)
    broker.start()

    stop_spot_refresh = threading.Event()

    def reconcile_spot_quote() -> None:
        last_attempt = 0.0
        last_error_at = 0.0
        while not stop_spot_refresh.wait(1):
            if market_session_has_ended():
                break
            now = time.monotonic()
            if now - last_attempt < 5:
                continue
            last_attempt = now
            try:
                process_spot_quote(
                    broker.nifty_spot_ltp(),
                    pd.Timestamp.now(tz=settings.market_timezone),
                )
            except Exception as exc:
                if now - last_error_at >= 60:
                    print(f"NIFTY spot REST fallback failed: {exc}", flush=True)
                    last_error_at = now

    threading.Thread(
        target=reconcile_spot_quote,
        name="nifty-spot-refresh",
        daemon=True,
    ).start()

    equity_history_end = pd.Timestamp.now(tz=settings.market_timezone).to_pydatetime()
    equity_history_start = equity_history_end - timedelta(days=5)
    for equity_index, (symbol, token) in enumerate(equity_tokens_by_symbol.items()):
        if equity_index:
            time.sleep(0.4)
        try:
            equity_history: list[dict[str, Any]] = broker.historical_data(
                token, equity_history_start, equity_history_end, "minute"
            )
            if not equity_history:
                print(f"No historical candles for {symbol}; waiting for live warm-up")
                continue
            equity_engine = equity_engines[symbol]
            with equity_engine_locks[symbol]:
                equity_frames: CandleFrames = equity_engine.load_history(equity_history)
                current_five_bucket = pd.Timestamp.now(
                    tz=settings.market_timezone
                ).floor("5min")
                closed_five_history = equity_frames["5min"].loc[
                    equity_frames["5min"].index < current_five_bucket
                ]
                equity_history_rows = [
                    equity_candle_record(
                        symbol,
                        timeframe,
                        cast(pd.Timestamp, timestamp),
                        row,
                    )
                    for timeframe, frame in equity_frames.items()
                    for timestamp, row in (
                        frame.iterrows()
                        if timeframe == "1min"
                        else closed_five_history.iterrows()
                    )
                ]
                if not closed_five_history.empty:
                    last_equity_persisted_five_minute[symbol] = cast(
                        pd.Timestamp, closed_five_history.index[-1]
                    )
            insert_equity_candles(equity_history_rows)
            print(
                f"Equity warm-up {symbol}: {len(equity_frames['1min'])} 1m bars, "
                f"{len(equity_frames['5min'])} 5m bars"
            )
        except Exception as exc:
            print(f"Equity history warm-up failed for {symbol}: {exc}")

    while True:
        market_now = datetime.now(ZoneInfo(settings.market_timezone))
        close_time = market_now.replace(hour=15, minute=30, second=0, microsecond=0)
        seconds_until_close = max(1.0, (close_time - market_now).total_seconds())
        time.sleep(min(max(1, settings.news_refresh_seconds), seconds_until_close))
        if market_session_has_ended():
            broker.stop()
            stop_spot_refresh.set()
            expire_stale_equity_signals(market_now, expire_all_open=True)
            closed_reason = "NSE session ended at 15:30 IST; live market data is closed."
            update_market_status(
                "CLOSED",
                last_error="",
                analysis_state="CLOSED",
                analysis_reason=closed_reason,
                analysis_updated_at=utc_now(),
            )
            print(closed_reason, flush=True)
            break
        try:
            news_bias = refresh_news()
            print("News bias refreshed:", news_bias)
        except Exception as exc:
            print("News refresh error:", exc)

if __name__ == "__main__":
    main()
