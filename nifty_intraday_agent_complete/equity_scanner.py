import csv
import math
from pathlib import Path
import os
from typing import Any, Callable, Mapping, cast

import pandas as pd

from accuracy_config import quality_settings as quality
from equity_research import summarize

MIN_REWARD_RISK = float(os.getenv("EQUITY_MIN_REWARD_RISK", "1.5"))
from indicators import detect_latest_candlestick_patterns
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
        "ema9_slope", "ema21_slope", "rsi14", "vwap", "atr14", "atr_pct",
        "adx14", "plus_di14", "minus_di14", "relative_volume20",
        "body_pct", "close_location", "prior_day_high", "prior_day_low",
        "opening_range_high", "opening_range_low",
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
    one_minute_pattern_rows: dict[pd.Timestamp, list[dict[str, str]]] | None = None,
    five_minute_pattern_rows: dict[pd.Timestamp, list[dict[str, str]]] | None = None,
    premarket_context: Mapping[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    research_provider: Callable[[float], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    def reject(reason: str) -> None:
        if diagnostics is not None:
            diagnostics["reason"] = reason

    minute_frame = frames["1min"]
    five_minute_frame = frames["5min"]
    if minute_frame.empty or five_minute_frame.empty:
        reject("Waiting for 1-minute and 5-minute candle history")
        return None

    current_minute = cast(pd.Timestamp, minute_frame.index[-1])
    closed_minutes: pd.DataFrame = minute_frame.loc[
        minute_frame.index < current_minute
    ]
    closed_five_minutes: pd.DataFrame = five_minute_frame.loc[
        five_minute_frame.index < current_minute.floor("5min")
    ]
    if closed_minutes.empty or closed_five_minutes.empty:
        reject("Waiting for closed 1-minute and 5-minute candles")
        return None

    latest_one_minute = cast(pd.Timestamp, closed_minutes.index[-1])
    one_minute_patterns = (
        detect_latest_candlestick_patterns(closed_minutes)
        if one_minute_pattern_rows is None
        else one_minute_pattern_rows.get(latest_one_minute, [])
    )
    analysis = build_signal(
        closed_minutes,
        closed_five_minutes,
        news_bias,
        "NEUTRAL",
        candlestick_patterns=one_minute_patterns,
        premarket_context=premarket_context,
    )
    if analysis["signal"] != "CALL":
        reason = str(analysis.get("reason") or "no qualifying bullish setup")
        reject(f"Signal engine returned {analysis['signal']}: {reason}")
        return None
    relative_volume = analysis.get("relative_volume")
    if relative_volume is None or float(relative_volume) < quality.relative_volume_threshold:
        reject(
            "Relative volume below threshold"
            if relative_volume is not None else "Relative volume unavailable"
        )
        return None

    five_minute_patterns = (
        detect_latest_candlestick_patterns(closed_five_minutes)
        if five_minute_pattern_rows is None
        else five_minute_pattern_rows.get(
            cast(pd.Timestamp, closed_five_minutes.index[-1]), []
        )
    )
    matched_patterns = one_minute_patterns + five_minute_patterns
    bullish_patterns = [
        pattern for pattern in matched_patterns
        if pattern["direction"] == "BULLISH"
    ]
    bearish_patterns = [
        pattern for pattern in matched_patterns
        if pattern["direction"] == "BEARISH"
    ]
    # Candlestick patterns are optional supporting evidence only; they never
    # gate or veto an equity BUY signal.

    atr = analysis.get("atr")
    if atr is None or pd.isna(atr) or float(atr) <= 0:
        reject("ATR unavailable or non-positive")
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
    target_points = float(signal["target_price"]) - float(signal["entry_price"])
    if target_points < quality.equity_min_target_points:
        reject(
            f"Target only {target_points:.2f} points above entry; "
            f"minimum is {quality.equity_min_target_points:g}"
        )
        return None
    risk_points = float(signal["entry_price"]) - float(signal["stop_loss"] or 0)
    if risk_points <= 0:
        reject("Stop-loss unavailable")
        return None
    reward_risk = target_points / risk_points
    if reward_risk < MIN_REWARD_RISK:
        reject(f"Reward/risk {reward_risk:.2f} below minimum {MIN_REWARD_RISK:g}")
        return None
    signal["reward_risk"] = round(reward_risk, 2)

    # Research runs last because it may fetch fundamentals over the network.
    if research_provider is not None:
        research = research_provider(float(live_price))
        signal["research"] = research
        if research["verdict"] != "SUPPORTIVE":
            reject(f"Research {research['verdict']}: {summarize(research)}")
            return None
        signal["reason"] = f"{signal['reason']}; {summarize(research)}"
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
        f"{signal['reason']}; "
        + (
            "optional candlestick support: "
            f"{', '.join(sorted({pattern['name'] for pattern in bullish_patterns}))}; "
            if bullish_patterns and not bearish_patterns else ""
        )
        + "estimated holding time uses recent 1-minute price movement"
    )
    signal["feature_snapshot"] = {
        "version": 2,
        "symbol": symbol,
        "signal_candle_time": signal["signal_candle_time"],
        "entry_price": float(live_price),
        "market_regime": analysis.get("market_regime"),
        "technical_score": analysis.get("technical_score"),
        "context_score": analysis.get("context_score"),
        "total_score": analysis.get("total_score"),
        "news_bias": news_bias,
        "premarket_context": dict(analysis.get("premarket_context") or {}),
        "one_minute": _feature_row_snapshot(closed_minutes),
        "five_minute": _feature_row_snapshot(closed_five_minutes),
        "research": signal.get("research"),
        "reward_risk": signal.get("reward_risk"),
        "one_minute_patterns": one_minute_patterns,
        "five_minute_patterns": five_minute_patterns,
    }
    if diagnostics is not None:
        diagnostics["reason"] = "BUY setup passed all scanner filters"
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