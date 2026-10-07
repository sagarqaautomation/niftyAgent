from typing import Any

import pandas as pd

from config import settings
from indicators import candle_strength, detect_latest_candlestick_patterns


def _empty(reason: str, state: str = "WARMING_UP") -> dict[str, Any]:
    return {
        "signal": "WAIT",
        "technical_score": 0,
        "context_score": 0,
        "total_score": 0,
        "reason": reason,
        "analysis_state": state,
    }


def _value(row: pd.Series[Any], name: str, default: float = float("nan")) -> float:
    value = row.get(name, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _regime(df5: pd.DataFrame) -> str:
    row = df5.iloc[-1]
    adx = _value(row, "adx14")
    ema9_slope = _value(row, "ema9_slope")
    ema21_slope = _value(row, "ema21_slope")
    atr_pct = _value(row, "atr_pct")

    if pd.isna(adx) or pd.isna(ema9_slope) or pd.isna(ema21_slope):
        return "UNKNOWN"
    if atr_pct >= settings.high_volatility_atr_pct:
        return "HIGH_VOLATILITY"
    if adx >= settings.strong_trend_adx and ema9_slope * ema21_slope > 0:
        return "TRENDING"
    if adx >= settings.min_trend_adx:
        return "TRANSITION"
    return "RANGE"


def _structure_score(row: pd.Series[Any]) -> tuple[int, int, list[str]]:
    bull = bear = 0
    reasons: list[str] = []
    close = _value(row, "close")

    prior_high = _value(row, "prior_day_high")
    prior_low = _value(row, "prior_day_low")
    or_high = _value(row, "opening_range_high")
    or_low = _value(row, "opening_range_low")

    if not pd.isna(prior_high) and close > prior_high:
        bull += 1
        reasons.append("price above prior-day high")
    elif not pd.isna(prior_low) and close < prior_low:
        bear += 1
        reasons.append("price below prior-day low")
    elif not pd.isna(or_high) and close > or_high:
        bull += 1
        reasons.append("opening-range breakout")
    elif not pd.isna(or_low) and close < or_low:
        bear += 1
        reasons.append("opening-range breakdown")

    return bull, bear, reasons


def build_signal(
    df1: pd.DataFrame,
    df5: pd.DataFrame,
    news_bias: str = "NEUTRAL",
    option_bias: str = "NEUTRAL",
) -> dict[str, Any]:
    required_history = max(40, settings.minimum_history_bars)
    if len(df1) < required_history or len(df5) < 40:
        return _empty("Not enough candle history")

    a = df1.iloc[-1]
    t = df5.iloc[-1]

    required = (
        "vwap", "rsi14", "atr14", "adx14", "ema9", "ema21",
        "ema9_slope", "ema21_slope", "relative_volume20",
    )
    unavailable = [
        f"{field} is unavailable"
        for field in required
        if pd.isna(a.get(field)) and pd.isna(t.get(field))
    ]
    if unavailable:
        return _empty("; ".join(unavailable), "BLOCKED")

    if not df1["volume"].tail(20).gt(0).any():
        return _empty("no traded volume in the selected instrument", "BLOCKED")

    bull = 0
    bear = 0
    reasons: list[str] = []

    # 1. Higher-timeframe trend.
    t_ema9 = _value(t, "ema9")
    t_ema21 = _value(t, "ema21")
    t_ema9_slope = _value(t, "ema9_slope")
    t_ema21_slope = _value(t, "ema21_slope")
    adx = _value(t, "adx14")

    if t_ema9 > t_ema21:
        bull += 2
        reasons.append("5m EMA trend bullish")
    elif t_ema9 < t_ema21:
        bear += 2
        reasons.append("5m EMA trend bearish")

    if t_ema9_slope > 0 and t_ema21_slope > 0:
        bull += 1
        reasons.append("5m EMA slopes rising")
    elif t_ema9_slope < 0 and t_ema21_slope < 0:
        bear += 1
        reasons.append("5m EMA slopes falling")

    if adx >= settings.min_trend_adx:
        if t_ema9 > t_ema21:
            bull += 1
            reasons.append(f"trend strength confirmed (ADX {adx:.1f})")
        elif t_ema9 < t_ema21:
            bear += 1
            reasons.append(f"trend strength confirmed (ADX {adx:.1f})")

    # 2. Intraday VWAP alignment.
    close = _value(a, "close")
    vwap = _value(a, "vwap")
    if close > vwap:
        bull += 2
        reasons.append("1m price above VWAP")
    elif close < vwap:
        bear += 2
        reasons.append("1m price below VWAP")

    # 3. Momentum. Avoid rewarding overextended RSI.
    rsi = _value(a, "rsi14")
    if 52 <= rsi <= 68:
        bull += 1
        reasons.append("RSI supports bullish momentum")
    elif 32 <= rsi <= 48:
        bear += 1
        reasons.append("RSI supports bearish momentum")

    # 4. Relative volume. Baseline excludes the current candle.
    rel_volume = _value(a, "relative_volume20")
    if rel_volume >= settings.relative_volume_threshold:
        if close > _value(a, "open"):
            bull += 1
            reasons.append(f"relative volume expansion ({rel_volume:.2f}x)")
        elif close < _value(a, "open"):
            bear += 1
            reasons.append(f"relative volume expansion ({rel_volume:.2f}x)")

    # 5. Price structure.
    structure_bull, structure_bear, structure_reasons = _structure_score(a)
    bull += structure_bull
    bear += structure_bear
    reasons.extend(structure_reasons)

    # 6. Candle quality. Do not give points to a weak/doji candle.
    strength = candle_strength(a)
    if strength >= settings.minimum_candle_strength:
        if close > _value(a, "open"):
            bull += 1
            reasons.append("strong bullish candle")
        elif close < _value(a, "open"):
            bear += 1
            reasons.append("strong bearish candle")

    # 7. Candlestick patterns are confirmation only; never enough on their own.
    patterns = detect_latest_candlestick_patterns(df1)
    bullish_patterns = [p["name"] for p in patterns if p["direction"] == "BULLISH"]
    bearish_patterns = [p["name"] for p in patterns if p["direction"] == "BEARISH"]
    if bullish_patterns and not bearish_patterns and bull > bear:
        reasons.append("bullish candlestick confirmation")
    elif bearish_patterns and not bullish_patterns and bear > bull:
        reasons.append("bearish candlestick confirmation")

    technical_score = max(bull, bear)
    direction = "CALL" if bull > bear else "PUT" if bear > bull else "WAIT"
    regime = _regime(df5)

    # Range markets need stronger confirmation than trending markets.
    if regime == "RANGE" and technical_score < settings.range_market_min_score:
        direction = "WAIT"
        reasons.append(
            f"range market requires score >= {settings.range_market_min_score}"
        )

    if regime == "UNKNOWN":
        direction = "WAIT"
        reasons.append("market regime unavailable")

    if technical_score < settings.min_total_score:
        direction = "WAIT"
        reasons.append(
            f"score {technical_score}/{settings.min_total_score} below required threshold"
        )

    # Context is deliberately a veto/confirmation, not a large directional weight.
    context_score = 0
    if news_bias == "BULLISH":
        context_score += 1
    elif news_bias == "BEARISH":
        context_score -= 1
    if option_bias == "BULLISH":
        context_score += 1
    elif option_bias == "BEARISH":
        context_score -= 1
    context_score = max(-2, min(2, context_score))

    if direction == "CALL" and context_score <= -2:
        direction = "WAIT"
        reasons.append("strongly conflicting external context")
    elif direction == "PUT" and context_score >= 2:
        direction = "WAIT"
        reasons.append("strongly conflicting external context")

    # High volatility is not automatically bullish/bearish. Require stronger score.
    if regime == "HIGH_VOLATILITY" and technical_score < settings.high_volatility_min_score:
        direction = "WAIT"
        reasons.append(
            f"high-volatility market requires score >= {settings.high_volatility_min_score}"
        )

    total_score = technical_score + context_score
    atr = _value(a, "atr14")

    return {
        "signal": direction,
        "technical_score": int(technical_score),
        "context_score": int(context_score),
        "total_score": int(total_score),
        "reason": "; ".join(dict.fromkeys(reasons)) if reasons else "No strong setup",
        "analysis_state": direction,
        "news_bias": news_bias,
        "option_bias": option_bias,
        "market_regime": regime,
        "adx": adx if not pd.isna(adx) else None,
        "relative_volume": rel_volume if not pd.isna(rel_volume) else None,
        "pattern_confirmation": patterns,
        "entry_price": close if direction != "WAIT" else None,
        "atr": atr if direction != "WAIT" and not pd.isna(atr) else None,
    }


def add_risk_levels(signal: dict[str, Any]) -> dict[str, Any]:
    if signal["signal"] == "WAIT" or not signal.get("entry_price") or not signal.get("atr"):
        signal["target_price"] = None
        signal["stop_loss"] = None
        return signal

    entry = float(signal["entry_price"])
    risk = float(signal["atr"]) * settings.atr_sl_multiplier
    rr = max(settings.reward_risk, settings.minimum_reward_risk)

    if signal["signal"] == "CALL":
        signal["stop_loss"] = entry - risk
        signal["target_price"] = entry + risk * rr
    else:
        signal["stop_loss"] = entry + risk
        signal["target_price"] = entry - risk * rr

    signal["risk_per_trade_price"] = risk
    signal["reward_risk"] = rr
    return signal
