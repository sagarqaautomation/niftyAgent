"""Point-in-time constituent breadth/impact helpers for NIFTY context.

Weights are optional because current official index weights must be maintained
from a licensed/authoritative source. Without them the helper reports equal-
weighted breadth and labels the weighting method explicitly.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any, Mapping


def load_constituent_weights(path: str | Path = "data/nifty50_weights.csv") -> dict[str, float]:
    """Load optional CSV with columns symbol,weight_pct; ignore invalid rows."""
    source = Path(path)
    if not source.exists():
        return {}
    weights: dict[str, float] = {}
    with source.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("symbol", "")).strip().upper()
            try:
                weight = float(row.get("weight_pct", ""))
            except (TypeError, ValueError):
                continue
            if symbol and weight > 0:
                weights[symbol] = weight
    return weights


def summarize_constituent_returns(
    returns_pct: Mapping[str, float],
    weights: Mapping[str, float] | None = None,
    min_constituents: int = 10,
) -> dict[str, Any]:
    """Summarize completed 1-minute constituent returns without look-ahead.

    Returns are percentage points (e.g. 0.12 means +0.12%). A weighted mean
    is used only when positive weights cover the observations; otherwise the
    score is explicitly equal-weighted. A bias requires breadth confirmation.
    """
    clean: dict[str, float] = {}
    for symbol, value in returns_pct.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number and abs(number) != float("inf"):
            clean[str(symbol).upper()] = number

    count = len(clean)
    advancers = sum(value > 0 for value in clean.values())
    decliners = sum(value < 0 for value in clean.values())
    advance_ratio = advancers / count if count else 0.0
    decline_ratio = decliners / count if count else 0.0

    valid_weights = {
        symbol: float(weight)
        for symbol, weight in (weights or {}).items()
        if symbol in clean and float(weight) > 0
    }
    weights_cover_enough = len(valid_weights) >= max(3, math.ceil(count * 0.60))
    if valid_weights and weights_cover_enough:
        total_weight = sum(valid_weights.values())
        mean_return = sum(clean[s] * w for s, w in valid_weights.items()) / total_weight
        weighting = "index_weighted"
        weighted_coverage = len(valid_weights)
    elif clean:
        mean_return = sum(clean.values()) / count
        weighting = "equal_weighted_fallback"
        weighted_coverage = 0
    else:
        mean_return = 0.0
        weighting = "unavailable"
        weighted_coverage = 0

    bias = "NEUTRAL"
    if count >= max(1, min_constituents):
        if advance_ratio >= 0.60 and mean_return > 0:
            bias = "BULLISH"
        elif decline_ratio >= 0.60 and mean_return < 0:
            bias = "BEARISH"

    return {
        "bias": bias,
        "constituents_seen": count,
        "advancers": advancers,
        "decliners": decliners,
        "advance_ratio": round(advance_ratio, 4),
        "decline_ratio": round(decline_ratio, 4),
        "mean_return_pct": round(mean_return, 5),
        "weighting": weighting,
        "weighted_constituents": weighted_coverage,
    }
