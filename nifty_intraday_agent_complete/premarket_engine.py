"""Pre-market context scoring for NiftyAgent.

This module is deliberately provider-agnostic: callers must fetch licensed data
and pass normalized, timestamped observations. Missing inputs are omitted rather
than guessed. The output is context for the existing signal engine, not a trade
instruction. VIX measures volatility, not direction.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any, Mapping


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _direction(value: Any) -> float | None:
    """Normalize a directional input to [-1, 1]; reject values outside range."""
    number = _number(value)
    if number is None or not -1.0 <= number <= 1.0:
        return None
    return number


def build_premarket_context(
    *,
    gift_nifty_change_pct: Any = None,
    global_sentiment: Any = None,
    advance_decline_ratio: Any = None,
    news_sentiment: Any = None,
    india_vix: Any = None,
    india_vix_change_pct: Any = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Build a conservative pre-market summary from normalized observations.

    Inputs:
      - GIFT Nifty and VIX changes are percentage points (e.g. 0.4 = +0.4%).
      - global_sentiment/news_sentiment are normalized to [-1, 1].
      - advance_decline_ratio is advancing / declining; zero declines is not
        represented as infinity and should be passed as None.
      - observed_at is an ISO-8601 timestamp supplied by the data collector.

    Directional score weights are renormalized across available inputs:
    GIFT Nifty 35%, global sentiment 25%, breadth 25%, news 15%.
    """
    components: dict[str, float] = {}
    weights = {
        "gift_nifty": 0.35,
        "global_sentiment": 0.25,
        "market_breadth": 0.25,
        "news_sentiment": 0.15,
    }

    gift = _number(gift_nifty_change_pct)
    if gift is not None:
        # Saturate at +/-1.5% so a single extreme observation cannot dominate.
        components["gift_nifty"] = max(-1.0, min(1.0, gift / 1.5))

    global_value = _direction(global_sentiment)
    if global_value is not None:
        components["global_sentiment"] = global_value

    ratio = _number(advance_decline_ratio)
    if ratio is not None and ratio >= 0:
        # Map ratio 0.5 -> -0.5, 1.0 -> 0, 2.0 -> +0.5; cap extremes.
        components["market_breadth"] = max(-1.0, min(1.0, (ratio - 1.0) / 2.0))

    news_value = _direction(news_sentiment)
    if news_value is not None:
        components["news_sentiment"] = news_value

    available_weight = sum(weights[name] for name in components)
    score = (
        sum(components[name] * weights[name] for name in components) / available_weight
        if available_weight
        else 0.0
    )

    if len(components) < 2:
        bias = "INSUFFICIENT_DATA"
    elif score >= 0.20:
        bias = "BULLISH_BIAS"
    elif score <= -0.20:
        bias = "BEARISH_BIAS"
    else:
        bias = "NEUTRAL"

    vix = _number(india_vix)
    vix_change = _number(india_vix_change_pct)
    if vix is None or vix < 0:
        volatility_regime = "UNKNOWN"
    elif vix >= 20:
        volatility_regime = "HIGH"
    elif vix >= 15:
        volatility_regime = "ELEVATED"
    else:
        volatility_regime = "LOWER"

    risk_flags: list[str] = []
    if len(components) < 3:
        risk_flags.append("LIMITED_DIRECTIONAL_INPUTS")
    if vix is None:
        risk_flags.append("INDIA_VIX_UNAVAILABLE")
    elif vix >= 20:
        risk_flags.append("HIGH_VOLATILITY")
    if vix_change is not None and vix_change >= 10:
        risk_flags.append("VIX_RISING_SHARPLY")
    if observed_at is None:
        risk_flags.append("OBSERVATION_TIMESTAMP_MISSING")

    return {
        "observed_at": observed_at,
        "directional_bias": bias,
        "directional_score": round(score, 4),
        "components": {
            name: round(value, 4) for name, value in components.items()
        },
        "available_components": len(components),
        "volatility_regime": volatility_regime,
        "india_vix": vix,
        "india_vix_change_pct": vix_change,
        "risk_flags": risk_flags,
        "trade_instruction": "NONE",
        "notes": [
            "Context only; confirm with live price action after 09:15 IST.",
            "India VIX is a volatility measure, not a directional signal.",
            "Do not use current-session breadth before those data are actually available.",
            "Do not treat this score as a calibrated probability of profit.",
        ],
    }
