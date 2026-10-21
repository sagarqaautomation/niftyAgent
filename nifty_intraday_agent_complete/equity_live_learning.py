"""Research-only equity learning loop.

Trains a candidate from resolved equity BUY outcomes and evaluates it on a later
chronological holdout. It never replaces the live signal model or places orders.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings
from probability_engine import fit_probability_model, predict, save_model

MIN_SAMPLES = 100
RETRAIN_AFTER_NEW = 20


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _record(row_id: int, created_at: str, symbol: str, status: str,
            snapshot_raw: str | dict[str, Any]) -> dict[str, Any] | None:
    if status not in {"TARGET_HIT", "STOP_HIT"}:
        return None
    try:
        snapshot = json.loads(snapshot_raw) if isinstance(snapshot_raw, str) else snapshot_raw
    except (TypeError, ValueError):
        return None
    if not isinstance(snapshot, dict):
        return None
    minute = snapshot.get("one_minute") or {}
    if not isinstance(minute, dict):
        minute = {}
    candle_time = str(snapshot.get("signal_candle_time") or created_at)
    try:
        hour = datetime.fromisoformat(candle_time.replace("Z", "+00:00")).hour
    except ValueError:
        hour = 0
    close = _num(minute.get("close"))
    vwap = _num(minute.get("vwap"))
    vwap_distance = ((close - vwap) / vwap * 100.0) if vwap else 0.0
    opening_high = minute.get("opening_range_high")
    opening_low = minute.get("opening_range_low")
    structure = "NONE"
    if opening_high is not None and close > _num(opening_high):
        structure = "BREAKOUT_UP"
    elif opening_low is not None and close < _num(opening_low):
        structure = "BREAKDOWN"
    patterns = snapshot.get("one_minute_patterns") or []
    patterns += snapshot.get("five_minute_patterns") or []
    pattern_names = " ".join(
        str(item.get("name", "")).lower()
        for item in patterns if isinstance(item, dict)
    )
    return {
        "id": int(row_id),
        "timestamp": candle_time,
        "direction": "CALL",
        "regime": str(snapshot.get("market_regime") or "UNKNOWN"),
        "adx": _num(minute.get("adx14")),
        "rsi": _num(minute.get("rsi14"), 50.0),
        "relative_volume": _num(minute.get("relative_volume20")),
        "vwap_distance_pct": vwap_distance,
        "candle_strength": _num(minute.get("body_pct")),
        "hour": hour,
        "structure": structure,
        "pattern_hammer": "hammer" in pattern_names,
        "pattern_morning_star": "morning star" in pattern_names,
        "pattern_bullish_engulfing": "bullish engulfing" in pattern_names,
        "status": "SUCCESS" if status == "TARGET_HIT" else "FAILED",
        "symbol": symbol,
    }


def _metrics(labels: list[int], probabilities: list[float], baseline: float) -> dict[str, Any]:
    if not labels:
        return {"samples": 0}
    eps = 1e-6
    probs = [min(1 - eps, max(eps, p)) for p in probabilities]
    base = min(1 - eps, max(eps, baseline))
    brier = sum((p-y) ** 2 for p, y in zip(probs, labels)) / len(labels)
    base_brier = sum((base-y) ** 2 for y in labels) / len(labels)
    return {
        "samples": len(labels),
        "target_hits": sum(labels),
        "win_rate_pct": round(sum(labels) / len(labels) * 100, 2),
        "brier_score": round(brier, 6),
        "baseline_brier_score": round(base_brier, 6),
        "brier_improvement_vs_baseline": round(base_brier - brier, 6),
    }


def load_resolved_records(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    path = Path(db_path or settings.db_path)
    if not path.exists():
        return []
    with sqlite3.connect(str(path), timeout=10) as conn:
        rows = conn.execute("""
            SELECT id,created_at,symbol,status,feature_snapshot_json
            FROM equity_signals
            WHERE status IN ('TARGET_HIT','STOP_HIT')
              AND feature_snapshot_json IS NOT NULL
            ORDER BY created_at ASC,id ASC
        """).fetchall()
    records = []
    for row in rows:
        item = _record(*row)
        if item:
            records.append(item)
    return records


def maybe_train_candidate(
    db_path: str | Path | None = None,
    output_dir: str | Path = "data",
    min_samples: int = MIN_SAMPLES,
    retrain_after_new: int = RETRAIN_AFTER_NEW,
) -> dict[str, Any] | None:
    if os.getenv("LIVE_LEARNING_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    out = Path(output_dir)
    report_path = out / "equity_live_learning_report.json"
    records = load_resolved_records(db_path)
    if len(records) < min_samples:
        return {
            "status": "INSUFFICIENT_DATA",
            "resolved_samples": len(records),
            "minimum_samples_required": min_samples,
        }
    previous_count = 0
    try:
        previous_count = int(json.loads(report_path.read_text(encoding="utf-8")).get("resolved_samples", 0))
    except (OSError, ValueError, TypeError):
        pass
    if len(records) - previous_count < retrain_after_new:
        return None
    split = int(len(records) * 0.8)
    train, holdout = records[:split], records[split:]
    if not holdout or len({row["status"] for row in train}) < 2:
        return {"status": "INSUFFICIENT_CLASSES_OR_HOLDOUT", "resolved_samples": len(records)}
    model = fit_probability_model(train)
    labels, probabilities = [], []
    for row in holdout:
        probability, _ = predict(model, row)
        labels.append(1 if row["status"] == "SUCCESS" else 0)
        probabilities.append(float(probability if probability is not None else model.get("prior", 0.5)))
    validation = _metrics(labels, probabilities, float(model.get("prior", 0.5)))
    improved = validation.get("brier_improvement_vs_baseline", 0) > 0
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "research_only",
        "active_model_changed": False,
        "auto_order_placement": False,
        "status": "REVIEW_ONLY" if improved else "NO_VALIDATED_IMPROVEMENT",
        "resolved_samples": len(records),
        "new_resolved_since_previous_training": len(records) - previous_count,
        "train_samples": len(train),
        "holdout": validation,
        "train_period": [train[0]["timestamp"], train[-1]["timestamp"]],
        "holdout_period": [holdout[0]["timestamp"], holdout[-1]["timestamp"]],
        "candidate_model_path": str(out / "equity_research_candidate_model.json"),
        "promotion_rule": "Manual review only; require repeated out-of-sample improvement.",
    }
    out.mkdir(parents=True, exist_ok=True)
    save_model(model, out / "equity_research_candidate_model.json")
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report
