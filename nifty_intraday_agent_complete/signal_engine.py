from typing import Any

import pandas as pd

from config import settings
from accuracy_config import quality_settings as quality
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
    if atr_pct >= quality.high_volatility_atr_pct:
        return "HIGH_VOLATILITY"
    if adx >= quality.strong_trend_adx and ema9_slope * ema21_slope > 0:
        return "TRENDING"
    if adx >= quality.min_trend_adx:
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


def _precision_gate(
    direction: str,
    regime: str,
    technical_score: int,
    adx: float,
    t_ema9: float,
    t_ema21: float,
    t_ema9_slope: float,
    t_ema21_slope: float,
    rsi: float,
    candle_ok: bool,
    structure_bull: int,
    structure_bear: int,
) -> tuple[bool, list[str]]:
    if not quality.precision_mode or direction == "WAIT":
        return True, []

    failures: list[str] = []
    if regime != "TRENDING":
        failures.append("precision mode requires TRENDING regime")
    if technical_score < quality.precision_min_score:
        failures.append(f"precision score must be >= {quality.precision_min_score}")
    if pd.isna(adx) or adx < quality.precision_min_adx:
        failures.append(f"precision ADX must be >= {quality.precision_min_adx:g}")

    if direction == "CALL":
        if not (t_ema9 > t_ema21 and t_ema9_slope > 0 and t_ema21_slope > 0):
            failures.append("5m trend and slopes must all be bullish")
        if quality.precision_require_rsi and not (52 <= rsi <= 68):
            failures.append("RSI is not in the bullish confirmation band")
        if quality.precision_require_structure and structure_bull <= 0:
            failures.append("no bullish price-structure breakout")
    else:
        if not (t_ema9 < t_ema21 and t_ema9_slope < 0 and t_ema21_slope < 0):
            failures.append("5m trend and slopes must all be bearish")
        if quality.precision_require_rsi and not (32 <= rsi <= 48):
            failures.append("RSI is not in the bearish confirmation band")
        if quality.precision_require_structure and structure_bear <= 0:
            failures.append("no bearish price-structure breakdown")

    if quality.precision_require_candle and not candle_ok:
        failures.append("candle strength confirmation is missing")

    return not failures, failures


def build_signal(
    df1: pd.DataFrame,
    df5: pd.DataFrame,
    news_bias: str = "NEUTRAL",
    option_bias: str = "NEUTRAL",
    use_volume_confirmation: bool = True,
) -> dict[str, Any]:
    required_history = max(40, quality.minimum_history_bars)
    if len(df1) < required_history or len(df5) < 40:
        return _empty("Not enough candle history")

    a = df1.iloc[-1]
    t = df5.iloc[-1]

    required = (
        "vwap", "rsi14", "atr14", "adx14", "ema9", "ema21",
        "ema9_slope", "ema21_slope",
    )
    unavailable = [
        f"{field} is unavailable"
        for field in required
        if pd.isna(a.get(field)) and pd.isna(t.get(field))
    ]
    if unavailable:
        return _empty("; ".join(unavailable), "BLOCKED")

    volume_available = bool(df1["volume"].tail(20).gt(0).any())
    if use_volume_confirmation and not volume_available:
        return _empty("no traded volume in the selected instrument", "BLOCKED")

    bull = 0
    bear = 0
    reasons: list[str] = []

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

    if adx >= quality.min_trend_adx:
        if t_ema9 > t_ema21:
            bull += 1
            reasons.append(f"trend strength confirmed (ADX {adx:.1f})")
        elif t_ema9 < t_ema21:
            bear += 1
            reasons.append(f"trend strength confirmed (ADX {adx:.1f})")

    close = _value(a, "close")
    vwap = _value(a, "vwap")
    if close > vwap:
        bull += 2
        reasons.append("1m price above VWAP")
    elif close < vwap:
        bear += 2
        reasons.append("1m price below VWAP")

    rsi = _value(a, "rsi14")
    if 52 <= rsi <= 68:
        bull += 1
        reasons.append("RSI supports bullish momentum")
    elif 32 <= rsi <= 48:
        bear += 1
        reasons.append("RSI supports bearish momentum")

    rel_volume = _value(a, "relative_volume20")
    if use_volume_confirmation and rel_volume >= quality.relative_volume_threshold:
        if close > _value(a, "open"):
            bull += 1
            reasons.append(f"relative volume expansion ({rel_volume:.2f}x)")
        elif close < _value(a, "open"):
            bear += 1
            reasons.append(f"relative volume expansion ({rel_volume:.2f}x)")
    elif not use_volume_confirmation:
        reasons.append("volume confirmation disabled for NIFTY spot price-only backtest")

    structure_bull, structure_bear, structure_reasons = _structure_score(a)
    bull += structure_bull
    bear += structure_bear
    reasons.extend(structure_reasons)

    strength = candle_strength(a)
    candle_ok = strength >= quality.minimum_candle_strength
    if candle_ok:
        if close > _value(a, "open"):
            bull += 1
            reasons.append("strong bullish candle")
        elif close < _value(a, "open"):
            bear += 1
            reasons.append("strong bearish candle")

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

    if regime == "RANGE" and technical_score < quality.range_market_min_score:
        direction = "WAIT"
        reasons.append(f"range market requires score >= {quality.range_market_min_score}")

    if regime == "UNKNOWN":
        direction = "WAIT"
        reasons.append("market regime unavailable")

    if technical_score < settings.min_total_score:
        direction = "WAIT"
        reasons.append(
            f"score {technical_score}/{settings.min_total_score} below required threshold"
        )

    precision_ok, precision_failures = _precision_gate(
        direction,
        regime,
        technical_score,
        adx,
        t_ema9,
        t_ema21,
        t_ema9_slope,
        t_ema21_slope,
        rsi,
        candle_ok,
        structure_bull,
        structure_bear,
    )
    if not precision_ok:
        direction = "WAIT"
        reasons.extend(precision_failures)

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

    if regime == "HIGH_VOLATILITY" and technical_score < quality.high_volatility_min_score:
        direction = "WAIT"
        reasons.append(
            f"high-volatility market requires score >= {quality.high_volatility_min_score}"
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
        "precision_mode": quality.precision_mode,
        "precision_gate_passed": precision_ok,
    }


def add_risk_levels(signal: dict[str, Any]) -> dict[str, Any]:
    if signal["signal"] == "WAIT" or not signal.get("entry_price") or not signal.get("atr"):
        signal["target_price"] = None
        signal["stop_loss"] = None
        return signal

    entry = float(signal["entry_price"])
    risk = float(signal["atr"]) * settings.atr_sl_multiplier
    rr = max(settings.reward_risk, quality.minimum_reward_risk)

    if signal["signal"] == "CALL":
        signal["stop_loss"] = entry - risk
        signal["target_price"] = entry + risk * rr
    else:
        signal["stop_loss"] = entry + risk
        signal["target_price"] = entry - risk * rr

    signal["risk_per_trade_price"] = risk
    signal["reward_risk"] = rr
    return signal
