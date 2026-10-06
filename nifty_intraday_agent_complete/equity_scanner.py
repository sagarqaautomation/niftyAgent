import csv
import math
from pathlib import Path
from typing import Any, cast

import pandas as pd

from signal_engine import add_risk_levels, build_signal


def load_watchlist(path: str) -> list[str]:
    watchlist_path = Path(path)
    with watchlist_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or "symbol" not in {
            field.strip().lower() for field in reader.fieldnames
        }:
            raise ValueError(f"{watchlist_path} must contain a 'symbol' column")
        symbol_field = next(field for field in reader.fieldnames if field.strip().lower() == "symbol")
        return list(dict.fromkeys(
            row[symbol_field].strip().upper()
            for row in reader
            if row.get(symbol_field) and row[symbol_field].strip()
        ))


def resolve_equity_tokens(
    instruments: list[dict[str, Any]],
    symbols: list[str],
    exchange: str = "NSE",
) -> tuple[dict[str, int], list[str]]:
    requested = set(symbols)
    tokens = {
        str(instrument["tradingsymbol"]).upper(): int(instrument["instrument_token"])
        for instrument in instruments
        if instrument.get("exchange") == exchange
        and instrument.get("instrument_type") == "EQ"
        and instrument.get("tradingsymbol") in requested
        and instrument.get("instrument_token") is not None
    }
    matched = {symbol: tokens[symbol] for symbol in symbols if symbol in tokens}
    missing = [symbol for symbol in symbols if symbol not in tokens]
    return matched, missing

def _feature_row_snapshot(frame: pd.DataFrame) -> dict[str, float | None]:
    row = frame.iloc[-1]
    fields = (
        "open", "high", "low", "close", "volume", "ema9", "ema21",
        "rsi14", "vwap", "atr14",
    )
    return {
        field: float(row[field]) if field in row.index and pd.notna(row[field]) else None
        for field in fields
    }


def build_equity_buy_signal(
    symbol: str,
    frames: dict[str, pd.DataFrame],
    live_price: float,
    news_bias: str = "NEUTRAL",
) -> dict[str, Any] | None:
    minute_frame = frames["1min"]
    five_minute_frame = frames["5min"]
    if minute_frame.empty or five_minute_frame.empty:
        return None

    current_minute = cast(pd.Timestamp, minute_frame.index[-1])
    closed_minutes: pd.DataFrame = minute_frame.loc[
        minute_frame.index < current_minute
    ]
    closed_five_minutes: pd.DataFrame = five_minute_frame.loc[
        five_minute_frame.index < current_minute.floor("5min")
    ]
    if closed_minutes.empty or closed_five_minutes.empty:
        return None

    analysis = build_signal(closed_minutes, closed_five_minutes, news_bias, "NEUTRAL")
    if analysis["signal"] != "CALL":
        return None

    atr = analysis.get("atr")
    if atr is None or pd.isna(atr) or float(atr) <= 0:
        return None

    signal = dict(analysis)
    signal.update({
        "signal": "CALL",
        "symbol": symbol,
        "signal_instrument": symbol,
        "signal_candle_time": cast(pd.Timestamp, closed_minutes.index[-1]).isoformat(),
        "entry_price": float(live_price),
        "atr": float(atr),
    })
    add_risk_levels(signal)
    signal["signal"] = "BUY"

    average_move: float = closed_minutes["close"].diff().abs().tail(20).mean()
    if pd.isna(average_move) or float(average_move) <= 0:
        signal["expected_holding_minutes"] = None
    else:
        distance = float(signal["target_price"]) - float(signal["entry_price"])
        signal["expected_holding_minutes"] = max(
            1, math.ceil(distance / float(average_move))
        )

    signal["reason"] = (
        f"{signal['reason']}; estimated holding time uses recent 1-minute price movement"
    )
    signal["feature_snapshot"] = {
        "version": 1,
        "symbol": symbol,
        "signal_candle_time": signal["signal_candle_time"],
        "entry_price": float(live_price),
        "news_bias": news_bias,
        "one_minute": _feature_row_snapshot(closed_minutes),
        "five_minute": _feature_row_snapshot(closed_five_minutes),
    }
    return signal


def format_equity_signal(signal: dict[str, Any]) -> str:
    expected_minutes = signal.get("expected_holding_minutes")
    holding_text = (
        f"about {expected_minutes} minutes"
        if expected_minutes is not None else "not available"
    )
    return (
        "EQUITY BUY RESEARCH ALERT\n"
        f"Stock: {signal['symbol']}\n"
        f"Entry: {signal['entry_price']:.2f}\n"
        f"Target: {signal['target_price']:.2f}\n"
        f"Stop loss: {signal['stop_loss']:.2f}\n"
        f"Estimated holding time: {holding_text}\n"
        f"Score: {signal['total_score']}\n"
        f"Reason: {signal['reason']}\n"
        "Research alert only; prices and timing are estimates."
    )