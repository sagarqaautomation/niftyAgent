"""Leakage-aware chronological ML benchmark for NIFTY research datasets.

Input CSV must contain a timestamp, a binary target (0/1), and explicitly
selected numeric features. The target should represent a resolved strategy
outcome, not a future value accidentally included among features.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

DEFAULT_TIMESTAMP = "timestamp"
DEFAULT_TARGET = "target"


def load_dataset(
    path: str | Path,
    features: list[str],
    timestamp_col: str = DEFAULT_TIMESTAMP,
    target_col: str = DEFAULT_TARGET,
) -> pd.DataFrame:
    """Load, validate, and chronologically sort an explicitly specified dataset."""
    if not features:
        raise ValueError("Select at least one numeric feature explicitly.")
    if len(features) != len(set(features)):
        raise ValueError("Feature names must be unique.")
    if timestamp_col in features or target_col in features:
        raise ValueError("Timestamp and target columns cannot be model features.")

    frame = pd.read_csv(path)
    required = {timestamp_col, target_col, *features}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Missing required CSV columns: {', '.join(missing)}")

    frame[timestamp_col] = pd.to_datetime(frame[timestamp_col], errors="coerce", utc=True)
    frame[target_col] = pd.to_numeric(frame[target_col], errors="coerce")
    frame = frame.dropna(subset=[timestamp_col, target_col]).copy()
    if not frame[target_col].isin([0, 1]).all():
        raise ValueError(f"{target_col} must contain only binary labels 0 and 1.")

    for name in features:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
        frame[name] = frame[name].replace([np.inf, -np.inf], np.nan)

    # Rows without labels are removed; missing feature values are imputed within
    # each fitted pipeline, so validation/test statistics cannot leak into train.
    frame = frame.sort_values(timestamp_col, kind="stable")
    frame = frame.drop_duplicates(subset=[timestamp_col], keep="last")
    frame = frame.reset_index(drop=True)
    if len(frame) < 30:
        raise ValueError("Need at least 30 labeled, timestamped rows for a meaningful experiment.")
    if frame[target_col].nunique() != 2:
        raise ValueError("Dataset must contain both target classes (0 and 1).")
    return frame


def build_models() -> dict[str, Pipeline]:
    """All preprocessing is inside the estimator pipeline to avoid leakage."""
    return {
        "majority_baseline": Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("model", DummyClassifier(strategy="prior")),
        ]),
        "logistic_regression": Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(class_weight="balanced", max_iter=2000, random_state=42)),
        ]),
        "rbf_svm": Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("model", SVC(kernel="rbf", C=1.0, gamma="scale", class_weight="balanced")),
        ]),
    }


def classification_metrics(y_true: pd.Series | np.ndarray, y_pred: np.ndarray,
                           scores: np.ndarray | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "samples": int(len(y_true)),
        "positive_rate_percent": round(float(np.mean(y_true)) * 100, 3),
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 5),
        "balanced_accuracy": round(float(balanced_accuracy_score(y_true, y_pred)), 5),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 5),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 5),
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 5),
    }
    if scores is not None and len(np.unique(y_true)) == 2:
        result["roc_auc"] = round(float(roc_auc_score(y_true, scores)), 5)
    else:
        result["roc_auc"] = None
    return result


def run_experiment(
    frame: pd.DataFrame,
    features: list[str],
    target_col: str = DEFAULT_TARGET,
    timestamp_col: str = DEFAULT_TIMESTAMP,
    folds: int = 4,
    holdout_fraction: float = 0.20,
    gap: int = 0,
) -> dict[str, Any]:
    """Compare models using expanding chronological folds and a final holdout."""
    if folds < 2:
        raise ValueError("folds must be at least 2.")
    if not 0.1 <= holdout_fraction <= 0.4:
        raise ValueError("holdout_fraction must be between 0.1 and 0.4.")
    if gap < 0:
        raise ValueError("gap must be >= 0.")

    frame = frame.sort_values(timestamp_col, kind="stable").reset_index(drop=True)
    split_at = int(len(frame) * (1.0 - holdout_fraction))
    development, holdout = frame.iloc[:split_at], frame.iloc[split_at:]
    if len(development) < folds + 10 or len(holdout) < 5:
        raise ValueError("Not enough rows for requested folds and holdout.")
    if development[target_col].nunique() != 2 or holdout[target_col].nunique() != 2:
        raise ValueError("Both classes must occur in development and final holdout periods.")

    X_dev = development[features]
    y_dev = development[target_col].astype(int)
    X_hold = holdout[features]
    y_hold = holdout[target_col].astype(int)
    splitter = TimeSeriesSplit(n_splits=folds, gap=gap)
    fold_reports: dict[str, list[dict[str, Any]]] = {name: [] for name in build_models()}

    for fold_no, (train_idx, val_idx) in enumerate(splitter.split(X_dev), start=1):
        X_train, X_val = X_dev.iloc[train_idx], X_dev.iloc[val_idx]
        y_train, y_val = y_dev.iloc[train_idx], y_dev.iloc[val_idx]
        if y_train.nunique() != 2 or y_val.nunique() != 2:
            continue
        for name, model in build_models().items():
            model.fit(X_train, y_train)
            pred = model.predict(X_val)
            scores = None
            if hasattr(model, "predict_proba"):
                scores = model.predict_proba(X_val)[:, 1]
            elif hasattr(model, "decision_function"):
                scores = model.decision_function(X_val)
            report = classification_metrics(y_val, pred, scores)
            report.update({
                "fold": fold_no,
                "train_start": str(development.iloc[train_idx[0]][timestamp_col]),
                "train_end": str(development.iloc[train_idx[-1]][timestamp_col]),
                "validation_start": str(development.iloc[val_idx[0]][timestamp_col]),
                "validation_end": str(development.iloc[val_idx[-1]][timestamp_col]),
            })
            fold_reports[name].append(report)

    holdout_reports: dict[str, Any] = {}
    for name, model in build_models().items():
        model.fit(X_dev, y_dev)
        pred = model.predict(X_hold)
        scores = None
        if hasattr(model, "predict_proba"):
            scores = model.predict_proba(X_hold)[:, 1]
        elif hasattr(model, "decision_function"):
            scores = model.decision_function(X_hold)
        report = classification_metrics(y_hold, pred, scores)
        report.update({
            "train_start": str(development.iloc[0][timestamp_col]),
            "train_end": str(development.iloc[-1][timestamp_col]),
            "holdout_start": str(holdout.iloc[0][timestamp_col]),
            "holdout_end": str(holdout.iloc[-1][timestamp_col]),
        })
        holdout_reports[name] = report

    return {
        "experiment": "chronological_nifty_ml_benchmark_v1",
        "rows": len(frame),
        "features": features,
        "target": target_col,
        "target_definition_note": "Confirm target is a resolved strategy outcome and all features were available at decision time.",
        "development_rows": len(development),
        "final_holdout_rows": len(holdout),
        "folds_requested": folds,
        "folds_evaluated": {name: len(items) for name, items in fold_reports.items()},
        "walk_forward": fold_reports,
        "final_holdout": holdout_reports,
        "limitations": [
            "Classification metrics do not establish trading profitability.",
            "Use point-in-time features, resolved labels, realistic fees/slippage, and trade-level P&L before adopting a model.",
            "The final holdout should not be repeatedly used for tuning.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Leakage-aware NIFTY classification benchmark.")
    parser.add_argument("csv", help="CSV containing timestamp, target (0/1), and numeric features.")
    parser.add_argument("--features", required=True,
                        help="Comma-separated explicit numeric feature allowlist; excludes target/timestamp.")
    parser.add_argument("--timestamp-col", default=DEFAULT_TIMESTAMP)
    parser.add_argument("--target-col", default=DEFAULT_TARGET)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--holdout-fraction", type=float, default=0.20)
    parser.add_argument("--gap", type=int, default=0,
                        help="Rows excluded between train and validation to reduce label-overlap leakage.")
    parser.add_argument("--output", default="reports/ml_benchmark.json")
    args = parser.parse_args()

    features = [part.strip() for part in args.features.split(",") if part.strip()]
    frame = load_dataset(args.csv, features, args.timestamp_col, args.target_col)
    report = run_experiment(frame, features, args.target_col, args.timestamp_col,
                            args.folds, args.holdout_fraction, args.gap)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(output), "rows": report["rows"],
                      "features": features, "final_holdout": report["final_holdout"]}, indent=2))


if __name__ == "__main__":
    main()
