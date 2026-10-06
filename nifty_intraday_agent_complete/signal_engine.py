from typing import Any

import pandas as pd

from indicators import candle_strength
from config import settings

def build_signal(
    df1: pd.DataFrame,
    df5: pd.DataFrame,
    news_bias: str = "NEUTRAL",
    option_bias: str = "NEUTRAL",
) -> dict[str, Any]:
    if len(df1) < 30 or len(df5) < 30:
        return {
            "signal": "WAIT", "technical_score": 0, "context_score": 0,
            "total_score": 0, "reason": "Not enough candle history",
            "analysis_state": "WARMING_UP",
        }

    a = df1.iloc[-1]
    t = df5.iloc[-1]
    unavailable = []
    if pd.isna(a["vwap"]):
        unavailable.append("VWAP is unavailable")
    volume_window = df1["volume"].fillna(0).tail(20)
    if not volume_window.gt(0).any():
        unavailable.append("no traded volume in the selected instrument")
    if unavailable:
        return {
            "signal": "WAIT", "technical_score": 0, "context_score": 0,
            "total_score": 0,
            "reason": "; ".join(unavailable),
            "analysis_state": "BLOCKED",
        }

    bull = 0
    bear = 0
    reasons = []

    if t["ema9"] > t["ema21"]:
        bull += 2
        reasons.append("5m EMA trend bullish")
    elif t["ema9"] < t["ema21"]:
        bear += 2
        reasons.append("5m EMA trend bearish")

    if a["close"] > a["vwap"]:
        bull += 2
        reasons.append("1m price above VWAP")
    elif a["close"] < a["vwap"]:
        bear += 2
        reasons.append("1m price below VWAP")

    if 55 <= a["rsi14"] <= 72:
        bull += 1
        reasons.append("RSI supports bullish momentum")
    elif 28 <= a["rsi14"] <= 45:
        bear += 1
        reasons.append("RSI supports bearish momentum")

    if len(df1) >= 20:
        avg_vol = df1["volume"].tail(20).mean()
        if avg_vol and a["volume"] > avg_vol * 1.2:
            if a["close"] > a["open"]:
                bull += 2
                reasons.append("bullish volume expansion")
            elif a["close"] < a["open"]:
                bear += 2
                reasons.append("bearish volume expansion")

    strength = candle_strength(a)
    if strength >= 0.65:
        if a["close"] > a["open"]:
            bull += 1
            reasons.append("strong bullish candle")
        elif a["close"] < a["open"]:
            bear += 1
            reasons.append("strong bearish candle")

    technical_score = max(bull, bear)

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

    if bull > bear and technical_score >= settings.min_total_score:
        direction = "CALL"
    elif bear > bull and technical_score >= settings.min_total_score:
        direction = "PUT"
    else:
        direction = "WAIT"

    total_score = technical_score + context_score

    if direction == "WAIT" and technical_score < settings.min_total_score:
        reasons.append(
            f"score {technical_score}/{settings.min_total_score} below required threshold"
        )

    if direction == "CALL" and context_score <= -2:
        direction = "WAIT"
        reasons.append("strongly conflicting context")
    elif direction == "PUT" and context_score >= 2:
        direction = "WAIT"
        reasons.append("strongly conflicting context")

    return {
        "signal": direction,
        "technical_score": int(technical_score),
        "context_score": int(context_score),
        "total_score": int(total_score),
        "reason": "; ".join(reasons) if reasons else "No strong setup",
        "analysis_state": direction,
        "news_bias": news_bias,
        "option_bias": option_bias,
        "entry_price": float(a["close"]) if direction != "WAIT" else None,
        "atr": float(a["atr14"]) if direction != "WAIT" and a["atr14"] == a["atr14"] else None,
    }

def add_risk_levels(signal: dict[str, Any]) -> dict[str, Any]:
    if signal["signal"] == "WAIT" or not signal.get("entry_price") or not signal.get("atr"):
        signal["target_price"] = None
        signal["stop_loss"] = None
        return signal

    entry = signal["entry_price"]
    risk = signal["atr"] * settings.atr_sl_multiplier

    if signal["signal"] == "CALL":
        signal["stop_loss"] = entry - risk
        signal["target_price"] = entry + risk * settings.reward_risk
    else:
        signal["stop_loss"] = entry + risk
        signal["target_price"] = entry - risk * settings.reward_risk

    return signal
