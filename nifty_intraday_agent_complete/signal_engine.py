from pathlib import Path
import json
from typing import Any

import pandas as pd

from config import settings
from accuracy_config import quality_settings as quality
from indicators import candle_strength, detect_latest_candlestick_patterns
from probability_engine import load_model, predict


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


def _setup_key(direction: str, regime: str, adx: float, rsi: float, rel_volume: float,
               vwap_distance_pct: float, candle_strength_value: float,
               structure: str, hour: int) -> str:
    adx_bin = "<20" if pd.isna(adx) or adx < 20 else "20-25" if adx < 25 else "25-30" if adx < 30 else "30+"
    rsi_bin = "<40" if pd.isna(rsi) or rsi < 40 else "40-50" if rsi < 50 else "50-60" if rsi < 60 else "60+"
    vol_bin = "<1" if pd.isna(rel_volume) or rel_volume < 1 else "1-1.25" if rel_volume < 1.25 else "1.25+"
    vwap_bin = "below-0.1" if vwap_distance_pct < -0.10 else "near" if vwap_distance_pct <= 0.10 else "above-0.1"
    candle_bin = "weak" if candle_strength_value < 0.50 else "medium" if candle_strength_value < 0.65 else "strong"
    return "|".join([direction, regime, adx_bin, rsi_bin, vol_bin, vwap_bin, candle_bin, structure, str(hour)])


def _historical_probability(signal_features: dict[str, Any]) -> tuple[float | None, int]:
    if not quality.probability_gate_enabled:
        return None, 0
    model = load_model(quality.setup_profiles_path)
    if not model:
        return None, 0
    return predict(model, signal_features)

