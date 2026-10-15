"""Research-only learning loop from resolved NiftyAgent live signals.

The live worker stores point-in-time feature snapshots and resolves signal outcomes.
This module periodically trains an offline candidate on older resolved outcomes and
reports performance on a later chronological holdout. It NEVER changes the active
signal engine or places orders; promotion remains a separate reviewed step.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings
from probability_engine import fit_probability_model, predict, save_model

DEFAULT_MIN_SAMPLES = 100
DEFAULT_RETRAIN_AFTER_NEW = 20
DEFAULT_TRAIN_FRACTION = 0.80


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_training_record(
    row_id: int,
    created_at: str,
    direction: str,
    status: str,
    snapshot_json: str | dict[str, Any],
) -> dict[str, Any] | None:
    """Convert a persisted point-in-time snapshot to the probability model schema."""
    if status not in {"SUCCESS", "FAILED"} or direction not in {"CALL", "PUT"}:
        return None
    try:
        snapshot = json.loads(snapshot_json) if isinstance(snapshot_json, str) else snapshot_json
    except (TypeError, ValueError):
        return None
    if not isinstance(snapshot, dict):
        return None

    minute = snapshot.get("one_minute") or {}
    if not isinstance(minute, dict):
        minute = {}
    close = _number(minute.get("close"))
    vwap = _number(minute.get("vwap"))
    vwap_distance = ((close - vwap) / vwap * 100.0) if close is not None and vwap else None
    candle_time = snapshot.get("signal_candle_time") or created_at
    try:
        parsed_time = datetime.fromisoformat(str(candle_time).replace("Z", "+00:00"))
        hour = parsed_time.hour
    except (TypeError, ValueError):
        try:
            hour = datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).hour
        except (TypeError, ValueError):
            hour = 0

    close_price = _number(minute.get("close"))
    opening_high = _number(minute.get("opening_range_high"))
    opening_low = _number(minute.get("opening_range_low"))
    structure = "NONE"
    if close_price is not None and opening_high is not None and close_price > opening_high:
        structure = "BREAKOUT_UP"
    elif close_price is not None and opening_low is not None and close_price < opening_low:
        structure = "BREAKDOWN"

    raw_patterns = snapshot.get("pattern_confirmation") or []
    if isinstance(raw_patterns, str):
        raw_patterns = [raw_patterns]
    pattern_text = " ".join(str(item).lower() for item in raw_patterns)

    return {
        "id": int(row_id),
        "timestamp": str(candle_time),
        "direction": direction,
        "regime": str(snapshot.get("market_regime") or "UNKNOWN"),
        "adx": _number(minute.get("adx14")) or 0.0,
        "rsi": _number(minute.get("rsi14")) if _number(minute.get("rsi14")) is not None else 50.0,
        "relative_volume": _number(minute.get("relative_volume20")) or 0.0,
        "vwap_distance_pct": vwap_distance or 0.0,
        "candle_strength": _number(minute.get("body_pct")) or 0.0,
        "hour": hour,
        "structure": structure,
        "pattern_hammer": "hammer" in pattern_text,
        "pattern_morning_star": "morning star" in pattern_text,
        "pattern_bullish_engulfing": "bullish engulfing" in pattern_text,
        "status": status,
    }


def load_resolved_records(db_path: str | Path) -> list[dict[str, Any]]:
    """Load only resolved CALL/PUT outcomes with their persisted feature snapshots."""
    path = Path(db_path)
    if not path.exists():
        return []
    with sqlite3.connect(str(path), timeout=10) as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, signal, status, feature_snapshot_json
            FROM signals
            WHERE status IN ('SUCCESS','FAILED')
              AND signal IN ('CALL','PUT')
              AND feature_snapshot_json IS NOT NULL
              AND (signal_instrument='NIFTY spot index' OR signal_instrument IS NULL)
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
    records = []
    for row_id, created_at, direction, status, snapshot_json in rows:
        record = build_training_record(row_id, created_at, direction, status, snapshot_json)
        if record is not None:
            records.append(record)
    records.sort(key=lambda item: (item["timestamp"], item["id"]))
    return records


def _metrics(labels: list[int], probabilities: list[float], baseline_probability: float) -> dict[str, Any]:
    if not labels:
        return {"samples": 0}
    eps = 1e-6
    clipped = [min(1 - eps, max(eps, p)) for p in probabilities]
    baseline = min(1 - eps, max(eps, baseline_probability))
    brier = sum((p - y) ** 2 for p, y in zip(clipped, labels)) / len(labels)
    baseline_brier = sum((baseline - y) ** 2 for y in labels) / len(labels)
    log_loss = -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(clipped, labels)) / len(labels)
    selected = [(y, p) for y, p in zip(labels, clipped) if p >= 0.60]
    return {
        "samples": len(labels),
        "wins": sum(labels),
        "win_rate_pct": round(sum(labels) / len(labels) * 100, 2),
        "brier_score": round(brier, 6),
        "baseline_brier_score": round(baseline_brier, 6),
        "brier_improvement_vs_baseline": round(baseline_brier - brier, 6),
        "log_loss": round(log_loss, 6),
        "predicted_probability_at_least_60pct": {
            "samples": len(selected),
            "wins": sum(y for y, _ in selected),
            "win_rate_pct": round(sum(y for y, _ in selected) / len(selected) * 100, 2) if selected else None,
        },
    }


def train_candidate(
    records: list[dict[str, Any]],
    output_dir: str | Path = "data",
    min_samples: int = DEFAULT_MIN_SAMPLES,
    train_fraction: float = DEFAULT_TRAIN_FRACTION,
) -> dict[str, Any]:
    """Train on the earlier segment, score a later holdout, and save a research-only candidate."""
    if not 0.5 <= train_fraction <= 0.9:
        raise ValueError("train_fraction must be between 0.5 and 0.9")
    resolved = [r for r in records if r.get("status") in {"SUCCESS", "FAILED"}]
    resolved.sort(key=lambda item: (str(item.get("timestamp", "")), int(item.get("id", 0))))
    total = len(resolved)
    report: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "research_only",
        "active_model_changed": False,
        "auto_order_placement": False,
        "resolved_samples": total,
        "minimum_samples_required": min_samples,
    }
    if total < min_samples:
        report.update({
            "status": "INSUFFICIENT_DATA",
            "message": f"Need at least {min_samples} resolved CALL/PUT outcomes with feature snapshots.",
        })
        return report

    split = int(total * train_fraction)
    train_rows, holdout_rows = resolved[:split], resolved[split:]
    if not holdout_rows:
        report.update({"status": "INSUFFICIENT_HOLDOUT", "message": "Chronological holdout is empty."})
        return report
    train_wins = sum(row["status"] == "SUCCESS" for row in train_rows)
    train_prior = (train_wins + 25.0) / (len(train_rows) + 50.0)
    if len({row["status"] for row in train_rows}) < 2:
        report.update({"status": "INSUFFICIENT_CLASSES", "message": "Training period must contain both wins and losses."})
        return report

    model = fit_probability_model(train_rows)
    labels: list[int] = []
    probabilities: list[float] = []
    for row in holdout_rows:
        probability, _evidence = predict(model, row)
        labels.append(1 if row["status"] == "SUCCESS" else 0)
        probabilities.append(float(probability if probability is not None else model.get("prior", train_prior)))

    validation = _metrics(labels, probabilities, float(model.get("prior", train_prior)))
    improved = validation.get("brier_improvement_vs_baseline", 0) > 0
    report.update({
        "status": "REVIEW_ONLY" if improved else "NO_VALIDATED_IMPROVEMENT",
        "train_samples": len(train_rows),
        "holdout_samples": len(holdout_rows),
        "train_period": [train_rows[0]["timestamp"], train_rows[-1]["timestamp"]],
        "holdout_period": [holdout_rows[0]["timestamp"], holdout_rows[-1]["timestamp"]],
        "train_win_rate_pct": round(train_wins / len(train_rows) * 100, 2),
        "holdout": validation,
        "promotion_rule": "Never auto-promote; require repeated out-of-sample improvement and manual review.",
        "candidate_model_path": str(Path(output_dir) / "research_candidate_model.json"),
    })
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    save_model(model, out / "research_candidate_model.json")
    return report


def maybe_train_candidate(
    db_path: str | Path | None = None,
    output_dir: str | Path = "data",
    min_samples: int = DEFAULT_MIN_SAMPLES,
    retrain_after_new: int = DEFAULT_RETRAIN_AFTER_NEW,
) -> dict[str, Any] | None:
    """Retrain only after enough new resolved outcomes; never updates the active model."""
    if os.getenv("LIVE_LEARNING_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    path = Path(db_path or settings.db_path)
    out = Path(output_dir)
    report_path = out / "live_learning_report.json"
    records = load_resolved_records(path)
    if len(records) < min_samples:
        return {
            "status": "INSUFFICIENT_DATA",
            "resolved_samples": len(records),
            "minimum_samples_required": min_samples,
        }
    previous_count = 0
    try:
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        previous_count = int(previous.get("resolved_samples", 0))
    except (OSError, ValueError, TypeError):
        pass
    if len(records) - previous_count < retrain_after_new:
        return None
    report = train_candidate(records, out, min_samples=min_samples)
    report["resolved_samples"] = len(records)
    report["new_resolved_since_previous_training"] = max(0, len(records) - previous_count)
    out.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and evaluate a research-only candidate from resolved NiftyAgent signals.")
    parser.add_argument("--db", default=settings.db_path, help="Path to the NiftyAgent SQLite database.")
    parser.add_argument("--output-dir", default="data", help="Directory for candidate model and JSON report.")
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    parser.add_argument("--retrain-after-new", type=int, default=DEFAULT_RETRAIN_AFTER_NEW)
    args = parser.parse_args()
    result = maybe_train_candidate(args.db, args.output_dir, args.min_samples, args.retrain_after_new)
    if result is None:
        print("No training run due: waiting for enough newly resolved outcomes.")
        return
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
