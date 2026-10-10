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
    min_weight_constituents: int = 40,
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
    configured_weights = {str(s).upper(): float(w) for s, w in (weights or {}).items() if float(w) > 0}
    complete_weight_file = (
        len(configured_weights) >= min_weight_constituents
        and 70.0 <= sum(configured_weights.values()) <= 120.0
    )
    weights_cover_enough = len(valid_weights) >= max(3, math.ceil(count * 0.60))
    if valid_weights and weights_cover_enough and complete_weight_file:
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


def summarize_constituent_news(
    items: list[Mapping[str, Any]],
    weights: Mapping[str, float] | None,
    max_age_minutes: int = 60,
    now_epoch: float | None = None,
    min_weight_constituents: int = 40,
) -> dict[str, Any]:
    """Aggregate fresh company headlines by their configured index weights.

    Headlines must have a publication timestamp and matched constituent symbols.
    Sentiment is clipped per headline to limit keyword-count outliers. Weight
    coverage is measured against the configured weights; without reliable
    weights this returns NEUTRAL rather than inventing an index impact.
    """
    import re
    import time

    configured = {
        str(symbol).upper(): float(weight)
        for symbol, weight in (weights or {}).items()
        if float(weight) > 0
    }
    if not configured:
        return {
            "bias": "NEUTRAL", "weighted_score": 0.0, "matched_headlines": 0,
            "matched_constituents": 0, "weighting": "unavailable",
        }

    total_weight = sum(configured.values())
    if len(configured) < min_weight_constituents or not 70.0 <= total_weight <= 120.0 or total_weight <= 0:
        return {
            "bias": "NEUTRAL", "weighted_score": 0.0, "matched_headlines": 0,
            "matched_constituents": 0, "weighting": "unavailable",
        }

    current = float(now_epoch if now_epoch is not None else time.time())
    max_age = max(1, int(max_age_minutes)) * 60
    seen: set[str] = set()
    matched_symbols: set[str] = set()
    score = 0.0
    matched_headlines = 0

    for item in items:
        try:
            published = float(item.get("published_epoch"))
            sentiment = float(item.get("sentiment", 0) or 0)
        except (TypeError, ValueError):
            continue
        age = current - published
        if age < 0 or age > max_age:
            continue
        title_key = re.sub(r"\\W+", " ", str(item.get("title", "")).lower()).strip()
        if not title_key or title_key in seen:
            continue
        symbols = {
            str(value).upper()
            for value in item.get("symbols", [])
            if str(value).upper() in configured
        }
        if not symbols:
            continue
        seen.add(title_key)
        clipped_sentiment = max(-3.0, min(3.0, sentiment))
        if clipped_sentiment == 0:
            continue
        for symbol in symbols:
            score += clipped_sentiment * configured[symbol] / total_weight
            matched_symbols.add(symbol)
        matched_headlines += 1

    bias = "NEUTRAL"
    if score >= 0.25:
        bias = "BULLISH"
    elif score <= -0.25:
        bias = "BEARISH"
    return {
        "bias": bias,
        "weighted_score": round(score, 4),
        "matched_headlines": matched_headlines,
        "matched_constituents": len(matched_symbols),
        "weighting": "index_weighted",
    }
