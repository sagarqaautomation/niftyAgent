"""Conservative, walk-forward trade-quality filter.

The model learns only from resolved historical trades in the training window.
It vetoes a signal only when a sufficiently sampled categorical context has a
statistically conservative upper win-rate bound below the training baseline.
"""

from __future__ import annotations

from math import sqrt
from typing import Any


FEATURES = (
    "direction",
    "regime",
    "direction_regime",
    "structure",
    "direction_structure",
    "hour_bucket",
    "score",
    "adx_bucket",
    "rsi_bucket",
)


def _wilson_upper(wins: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 1.0
    p = wins / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (centre + spread) / denom


def _hour_bucket(hour: int) -> str:
    if hour < 10:
        return "open"
    if hour < 12:
        return "morning"
    if hour < 14:
        return "midday"
    return "afternoon"


def _adx_bucket(adx: float | None) -> str:
    if adx is None:
        return "unknown"
    if adx < 18:
        return "weak"
    if adx < 25:
        return "medium"
    return "strong"


def _rsi_bucket(rsi: float | None) -> str:
    if rsi is None:
        return "unknown"
    if rsi < 40:
        return "oversold"
    if rsi < 50:
        return "bearish"
    if rsi <= 60:
        return "neutral"
    if rsi <= 70:
        return "bullish"
    return "overbought"


def feature_values(trade: dict[str, Any]) -> dict[str, str]:
    direction = str(trade.get("direction", "UNKNOWN"))
    regime = str(trade.get("regime", "UNKNOWN"))
    structure = str(trade.get("structure", "NONE"))
    hour = int(trade.get("hour", 0) or 0)
    score = int(trade.get("score", 0) or 0)
    adx = trade.get("adx")
    rsi = trade.get("rsi")
    return {
        "direction": direction,
        "regime": regime,
        "direction_regime": f"{direction}|{regime}",
        "structure": structure,
        "direction_structure": f"{direction}|{structure}",
        "hour_bucket": _hour_bucket(hour),
        "score": str(score),
        "adx_bucket": _adx_bucket(float(adx) if adx is not None else None),
        "rsi_bucket": _rsi_bucket(float(rsi) if rsi is not None else None),
    }


def fit_trade_quality_model(
    trades: list[dict[str, Any]],
    min_samples: int = 50,
    max_win_rate_gap: float = 0.08,
) -> dict[str, Any]:
    resolved = [
        t for t in trades
        if t.get("status") in {"SUCCESS", "FAILED"}
    ]
    wins = sum(t.get("status") == "SUCCESS" for t in resolved)
    baseline = wins / len(resolved) if resolved else 0.0

    buckets: dict[str, dict[str, dict[str, int]]] = {
        feature: {} for feature in FEATURES
    }
    for trade in resolved:
        values = feature_values(trade)
        for feature in FEATURES:
            key = values[feature]
            bucket = buckets[feature].setdefault(key, {"wins": 0, "total": 0})
            bucket["total"] += 1
            bucket["wins"] += int(trade.get("status") == "SUCCESS")

    vetoes: list[dict[str, Any]] = []
    for feature in FEATURES:
        for key, stats in buckets[feature].items():
            n = stats["total"]
            if n < min_samples:
                continue
            upper = _wilson_upper(stats["wins"], n)
            observed = stats["wins"] / n
            # Require the conservative upper bound to be meaningfully below
            # the training baseline. This avoids reacting to small samples.
            if upper < baseline - max_win_rate_gap:
                vetoes.append({
                    "feature": feature,
                    "value": key,
                    "samples": n,
                    "wins": stats["wins"],
                    "observed_win_rate": round(observed, 4),
                    "wilson_upper": round(upper, 4),
                })

    return {
        "version": 1,
        "samples": len(resolved),
        "baseline_win_rate": round(baseline, 4),
        "min_samples": min_samples,
        "max_win_rate_gap": max_win_rate_gap,
        "vetoes": vetoes,
    }


def evaluate_trade_quality(
    model: dict[str, Any] | None,
    trade: dict[str, Any],
) -> tuple[bool, list[str]]:
    if not model:
        return True, []

    values = feature_values(trade)
    vetoes = model.get("vetoes", [])
    failures: list[str] = []
    for veto in vetoes:
        if values.get(veto.get("feature")) == veto.get("value"):
            failures.append(
                f"historically weak {veto['feature']}={veto['value']} "
                f"({veto['observed_win_rate']:.1%}, n={veto['samples']})"
            )
    return not failures, failures
