"""Deterministic historical evaluator for the NiftyAgent signal engine.

The evaluator enters on the next 1-minute candle open. Signals are generated
only from candles available at the decision timestamp, avoiding look-ahead bias.
Only one position is active at a time.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import time as datetime_time
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from config import settings
from accuracy_config import quality_settings as quality
from indicators import add_indicators
from signal_engine import add_risk_levels, build_signal
from probability_engine import fit_probability_model, save_model
from trade_quality_engine import fit_trade_quality_model


@dataclass
class Trade:
    signal_time: str
    entry_time: str
    direction: str
    entry: float
    target: float
    stop: float
    score: int
    regime: str
    status: str
    exit_time: str | None
    exit_price: float | None
    r_multiple: float | None
    net_r_multiple: float | None
    setup_key: str | None = None
    hour: int | None = None
    adx: float | None = None
    rsi: float | None = None
    relative_volume: float | None = None
    vwap_distance_pct: float | None = None
    candle_strength: float | None = None
    structure: str | None = None


def load_ohlcv_csv(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    if frame["timestamp"].dt.tz is None:
        frame["timestamp"] = frame["timestamp"].dt.tz_localize(settings.market_timezone)
    else:
        frame["timestamp"] = frame["timestamp"].dt.tz_convert(settings.market_timezone)

    frame = frame.set_index("timestamp").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame[["open", "high", "low", "close", "volume"]].astype(float)


def _resample_5m(minute: pd.DataFrame) -> pd.DataFrame:
    return minute.resample("5min").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna()


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def net_r_multiple(
    gross_r_multiple: float | None,
    round_trip_cost_points: float,
    risk_points: float,
) -> float | None:
    if gross_r_multiple is None or risk_points <= 0:
        return None
    return gross_r_multiple - round_trip_cost_points / risk_points


def _profit_factor(r_values: list[float]) -> float | None:
    losses = abs(sum(value for value in r_values if value < 0))
    if losses == 0:
        return None
    return round(sum(value for value in r_values if value > 0) / losses, 4)


def _metrics_without_trades(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "trades"}


def _resolve_trade(trade: Trade, future: pd.DataFrame) -> Trade:
    if future.empty:
        trade.status = "EXPIRED"
        return trade

    expiry = pd.Timestamp(trade.entry_time) + pd.to_timedelta(
        settings.signal_expiry_minutes, unit="min"
    )
    future = future.loc[future.index <= expiry]

    for timestamp, bar in future.iterrows():
        timestamp = cast(pd.Timestamp, timestamp)
        target_hit = (
            bar["high"] >= trade.target
            if trade.direction == "CALL"
            else bar["low"] <= trade.target
        )
        stop_hit = (
            bar["low"] <= trade.stop
            if trade.direction == "CALL"
            else bar["high"] >= trade.stop
        )

        if target_hit and stop_hit:
            trade.status = "AMBIGUOUS"
            trade.exit_time = pd.Timestamp(timestamp).isoformat()
            trade.exit_price = float(bar["close"])
            return trade
        if target_hit:
            trade.status = "SUCCESS"
            trade.exit_time = pd.Timestamp(timestamp).isoformat()
            trade.exit_price = float(trade.target)
            trade.r_multiple = abs(trade.target - trade.entry) / abs(
                trade.entry - trade.stop
            )
            return trade
        if stop_hit:
            trade.status = "FAILED"
            trade.exit_time = pd.Timestamp(timestamp).isoformat()
            trade.exit_price = float(trade.stop)
            trade.r_multiple = -1.0
            return trade

    trade.status = "EXPIRED"
    return trade


def _session_cutoff() -> datetime_time:
    session_end_minutes = 15 * 60 + 30
    expiry_minutes = int(settings.signal_expiry_minutes)
    cutoff_minutes = session_end_minutes - expiry_minutes
    return datetime_time(cutoff_minutes // 60, cutoff_minutes % 60)


def run_backtest(
    frame: pd.DataFrame,
    probability_model: dict[str, Any] | None = None,
    label: str | None = None,
    progress: bool = False,
    probability_threshold: float | None = None,
    trade_quality_model: dict[str, Any] | None = None,
    minimum_technical_score: int | None = None,
    round_trip_cost_points: float = 0.0,
) -> dict[str, Any]:
    if round_trip_cost_points < 0:
        raise ValueError("round_trip_cost_points must be >= 0")
    if (
        minimum_technical_score is not None
        and minimum_technical_score < settings.min_total_score
    ):
        raise ValueError(
            f"minimum_technical_score must be >= {settings.min_total_score}"
        )

    minute = frame.copy()
    one = add_indicators(minute)
    five = add_indicators(_resample_5m(minute))

    trades: list[Trade] = []
    last_signal_bar: pd.Timestamp | None = None
    last_direction: str | None = None
    reset_bars = 0
    blocked_until: pd.Timestamp | None = None
    cutoff = _session_cutoff()

    for i in range(max(60, quality.minimum_history_bars), len(one) - 1):
        decision_time = one.index[i]

        if blocked_until is not None and decision_time <= blocked_until:
            continue
        if decision_time.time() >= cutoff:
            continue

        # Indicators are precomputed, so use bounded history to avoid an O(n²) hotspot.
        df1 = one.iloc[max(0, i - 1000) : i + 1]
        eligible_five = np.flatnonzero(
            five.index < decision_time.floor("5min")
        )[-300:]
        df5 = five.iloc[eligible_five]

        if len(df5) < 40:
            continue

        signal = build_signal(
            df1,
            df5,
            "NEUTRAL",
            "NEUTRAL",
            use_volume_confirmation=False,
            probability_model=probability_model,
            probability_threshold=probability_threshold,
            trade_quality_model=trade_quality_model,
        )
        direction = signal["signal"]

        if direction == "WAIT":
            reset_bars += 1
            if reset_bars >= quality.setup_reset_bars:
                last_direction = None
            continue

        reset_bars = 0
        if direction == last_direction:
            continue
        if (
            last_signal_bar is not None
            and decision_time - last_signal_bar
            < pd.to_timedelta(quality.signal_cooldown_minutes, unit="min")
        ):
            continue
        if (
            minimum_technical_score is not None
            and int(signal["technical_score"]) < minimum_technical_score
        ):
            last_signal_bar = decision_time
            last_direction = direction
            continue

        entry_time = one.index[i + 1]
        entry_price = float(one.iloc[i + 1]["open"])
        signal_for_risk = dict(signal)
        signal_for_risk["signal"] = direction
        signal_for_risk["entry_price"] = entry_price
        signal_for_risk["atr"] = float(one.iloc[i]["atr14"])
        add_risk_levels(signal_for_risk)

        if not signal_for_risk.get("target_price") or not signal_for_risk.get("stop_loss"):
            continue

        trade = Trade(
            signal_time=decision_time.isoformat(),
            entry_time=entry_time.isoformat(),
            direction=direction,
            entry=entry_price,
            target=float(signal_for_risk["target_price"]),
            stop=float(signal_for_risk["stop_loss"]),
            score=int(signal["technical_score"]),
            regime=str(signal.get("market_regime", "UNKNOWN")),
            status="OPEN",
            exit_time=None,
            exit_price=None,
            r_multiple=None,
            net_r_multiple=None,
            setup_key=signal.get("setup_key"),
            hour=int(decision_time.hour),
            adx=float(signal["adx"]) if signal.get("adx") is not None else None,
            rsi=_optional_float(one.iloc[i].get("rsi14")),
            relative_volume=float(signal["relative_volume"]) if signal.get("relative_volume") is not None else None,
            vwap_distance_pct=((entry_price - float(one.iloc[i]["vwap"])) / entry_price * 100) if not pd.isna(one.iloc[i]["vwap"]) else None,
            candle_strength=_optional_float(one.iloc[i].get("body_pct")),
            structure=("BULL_BREAKOUT" if not pd.isna(one.iloc[i].get("opening_range_high")) and float(one.iloc[i]["close"]) > float(one.iloc[i]["opening_range_high"]) else "BEAR_BREAKDOWN" if not pd.isna(one.iloc[i].get("opening_range_low")) and float(one.iloc[i]["close"]) < float(one.iloc[i]["opening_range_low"]) else "NONE"),
        )
        trade = _resolve_trade(trade, one.iloc[i + 1 :])
        trade.net_r_multiple = net_r_multiple(
            trade.r_multiple,
            round_trip_cost_points,
            abs(trade.entry - trade.stop),
        )
        trades.append(trade)

        if trade.exit_time:
            blocked_until = pd.Timestamp(trade.exit_time)
        else:
            blocked_until = pd.Timestamp(entry_time) + pd.to_timedelta(
                settings.signal_expiry_minutes, unit="min"
            )

        last_signal_bar = decision_time
        last_direction = direction

    resolved = [t for t in trades if t.status in {"SUCCESS", "FAILED"}]
    wins = sum(t.status == "SUCCESS" for t in resolved)
    losses = sum(t.status == "FAILED" for t in resolved)
    ambiguous = sum(t.status == "AMBIGUOUS" for t in trades)
    expired = sum(t.status == "EXPIRED" for t in trades)
    r_values = [t.r_multiple for t in resolved if t.r_multiple is not None]
    net_r_values = [
        t.net_r_multiple for t in resolved if t.net_r_multiple is not None
    ]

    by_regime: dict[str, dict[str, Any]] = {}
    for trade in trades:
        bucket = by_regime.setdefault(
            trade.regime,
            {"signals": 0, "wins": 0, "losses": 0, "ambiguous": 0, "expired": 0},
        )
        bucket["signals"] += 1
        bucket["wins"] += int(trade.status == "SUCCESS")
        bucket["losses"] += int(trade.status == "FAILED")
        bucket["ambiguous"] += int(trade.status == "AMBIGUOUS")
        bucket["expired"] += int(trade.status == "EXPIRED")
        resolved_regime = bucket["wins"] + bucket["losses"]
        bucket["win_rate"] = (
            round(bucket["wins"] / resolved_regime * 100, 2)
            if resolved_regime
            else None
        )

    return {
        "signals": len(trades),
        "wins": wins,
        "losses": losses,
        "ambiguous": ambiguous,
        "expired": expired,
        "resolved": len(resolved),
        "win_rate_percent": round(wins / len(resolved) * 100, 2) if resolved else None,
        "average_r": round(sum(r_values) / len(r_values), 4) if r_values else None,
        "profit_factor": _profit_factor(r_values),
        "round_trip_cost_points": round_trip_cost_points,
        "net_average_r": (
            round(sum(net_r_values) / len(net_r_values), 4)
            if net_r_values
            else None
        ),
        "net_profit_factor": _profit_factor(net_r_values),
        "by_regime": by_regime,
        "trades": [asdict(t) for t in trades],
    }


def walk_forward(
    frame: pd.DataFrame,
    folds: int = 5,
    progress: bool = False,
    probability_threshold: float = 0.50,
    score_thresholds: list[int] | None = None,
    round_trip_cost_points: float = 0.0,
) -> list[dict[str, Any]]:
    if folds < 2:
        raise ValueError("folds must be >= 2")
    if round_trip_cost_points < 0:
        raise ValueError("round_trip_cost_points must be >= 0")
    if score_thresholds is not None:
        if not score_thresholds:
            raise ValueError("score_thresholds cannot be empty")
        if any(score < settings.min_total_score for score in score_thresholds):
            raise ValueError(
                f"score thresholds must be >= {settings.min_total_score}"
            )
        score_thresholds = sorted(set(score_thresholds))

    boundaries = np.linspace(0, len(frame), folds + 1, dtype=int)
    chunks = [
        frame.iloc[boundaries[i] : boundaries[i + 1]]
        for i in range(folds)
    ]

    results: list[dict[str, Any]] = []
    total_folds = len(chunks) - 1
    for i in range(1, len(chunks)):
        train = pd.concat(chunks[:i])
        validation = chunks[i]

        def log(message: str) -> None:
            if progress:
                print(message, flush=True)

        log(f"\n[Fold {i}/{total_folds}] train={len(train):,} rows, validation={len(validation):,} rows")
        if score_thresholds is not None:
            score_reports: dict[str, Any] = {}
            for score in score_thresholds:
                log(
                    f"[Fold {i}/{total_folds}] Testing minimum technical score {score}..."
                )
                report = run_backtest(
                    validation,
                    label=f"fold-{i}-score-{score}",
                    minimum_technical_score=score,
                    round_trip_cost_points=round_trip_cost_points,
                )
                score_reports[str(score)] = _metrics_without_trades(report)
                log(
                    f"[Fold {i}/{total_folds}] Score {score}: "
                    f"{report.get('signals', 0)} signals, "
                    f"{report.get('win_rate_percent')}% win rate, "
                    f"net PF {report.get('net_profit_factor')}"
                )
            results.append(
                {
                    "fold": i,
                    "train_start": train.index[0].isoformat(),
                    "train_end": train.index[-1].isoformat(),
                    "validation_start": validation.index[0].isoformat(),
                    "validation_end": validation.index[-1].isoformat(),
                    "training_rows": len(train),
                    "round_trip_cost_points": round_trip_cost_points,
                    "score_threshold_results": score_reports,
                }
            )
            continue

        log(f"[Fold {i}/{total_folds}] Running validation baseline...")
        baseline = run_backtest(
            validation,
            label=f"fold-{i}-baseline",
            round_trip_cost_points=round_trip_cost_points,
        )
        log(f"[Fold {i}/{total_folds}] Baseline complete: {baseline.get('signals', 0)} signals, {baseline.get('win_rate_percent')}% win rate")

        log(f"[Fold {i}/{total_folds}] Building training trade history...")
        train_report = run_backtest(train, label=f"fold-{i}-train")
        log(f"[Fold {i}/{total_folds}] Fitting probability model...")
        model = fit_probability_model(train_report["trades"])
        log(f"[Fold {i}/{total_folds}] Model fitted: {model.get('samples', 0)} labeled trades")

        log(f"[Fold {i}/{total_folds}] Running probability-filtered validation...")

        def metrics(report: dict[str, Any]) -> dict[str, Any]:
            return {key: value for key, value in report.items() if key != "trades"}

        filtered = run_backtest(
            validation,
            probability_model=model,
            label=f"fold-{i}-filtered",
            probability_threshold=probability_threshold,
            round_trip_cost_points=round_trip_cost_points,
        )
        log(f"[Fold {i}/{total_folds}] Fitting trade-quality model...")
        quality_model = fit_trade_quality_model(train_report["trades"])
        log(
            f"[Fold {i}/{total_folds}] Quality model fitted: "
            f"{len(quality_model.get('vetoes', []))} vetoes"
        )
        log(f"[Fold {i}/{total_folds}] Running trade-quality validation...")
        quality_filtered = run_backtest(
            validation,
            label=f"fold-{i}-quality",
            trade_quality_model=quality_model,
            round_trip_cost_points=round_trip_cost_points,
        )
        log(
            f"[Fold {i}/{total_folds}] Quality complete: "
            f"{quality_filtered.get('signals', 0)} signals, "
            f"{quality_filtered.get('win_rate_percent')}% win rate"
        )

        log(
            f"[Fold {i}/{total_folds}] Filtered complete: "
            f"{filtered.get('signals', 0)} signals, "
            f"{filtered.get('win_rate_percent')}% win rate"
        )
        results.append(
            {
                "fold": i,
                "train_start": train.index[0].isoformat(),
                "train_end": train.index[-1].isoformat(),
                "validation_start": validation.index[0].isoformat(),
                "validation_end": validation.index[-1].isoformat(),
                "train_samples": model.get("samples", 0),
                "baseline": metrics(baseline),
                "probability_filtered": metrics(filtered),
                "trade_quality_filtered": metrics(quality_filtered),
                "improvement": {
                    "win_rate_points": round((filtered.get("win_rate_percent") or 0) - (baseline.get("win_rate_percent") or 0), 2),
                    "average_r_delta": round((filtered.get("average_r") or 0) - (baseline.get("average_r") or 0), 4),
                    "profit_factor_delta": round((filtered.get("profit_factor") or 0) - (baseline.get("profit_factor") or 0), 4),
                    "signals_kept": filtered.get("signals", 0),
                    "signals_baseline": baseline.get("signals", 0),
                    "quality_win_rate_points": round((quality_filtered.get("win_rate_percent") or 0) - (baseline.get("win_rate_percent") or 0), 2),
                    "quality_average_r_delta": round((quality_filtered.get("average_r") or 0) - (baseline.get("average_r") or 0), 4),
                    "quality_profit_factor_delta": round((quality_filtered.get("profit_factor") or 0) - (baseline.get("profit_factor") or 0), 4),
                    "quality_signals_kept": quality_filtered.get("signals", 0),
                },
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest the NiftyAgent signal engine"
    )
    parser.add_argument(
        "csv",
        help="OHLCV CSV with timestamp,open,high,low,close,volume",
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument(
        "--progress",
        action="store_true",
        help="print fold progress while running",
    )
    parser.add_argument(
        "--probability-threshold",
        type=float,
        default=0.50,
        help="minimum modeled win probability for walk-forward filtering",
    )
    parser.add_argument(
        "--score-sweep",
        type=int,
        nargs="+",
        metavar="SCORE",
        help="compare minimum technical scores on each walk-forward validation fold",
    )
    parser.add_argument(
        "--round-trip-cost-points",
        type=float,
        default=0.0,
        help="estimated total entry+exit costs in price points (default: 0)",
    )
    parser.add_argument(
        "--fit-probability-model",
        "--fit-profiles",
        dest="fit_probability_model",
        help="write a broad feature probability model (--fit-profiles is a legacy alias)",
    )
    args = parser.parse_args()
    if args.fit_probability_model and args.walk_forward:
        parser.error("--fit-probability-model cannot be combined with --walk-forward")
    if args.score_sweep and not args.walk_forward:
        parser.error("--score-sweep requires --walk-forward")

    frame = load_ohlcv_csv(args.csv)
    if args.walk_forward:
        result = walk_forward(
            frame,
            args.folds,
            progress=args.progress,
            probability_threshold=args.probability_threshold,
            score_thresholds=args.score_sweep,
            round_trip_cost_points=args.round_trip_cost_points,
        )
    else:
        result = run_backtest(
            frame,
            progress=args.progress,
            round_trip_cost_points=args.round_trip_cost_points,
        )
        if args.fit_probability_model:
            model = fit_probability_model(result["trades"])
            save_model(model, args.fit_probability_model)
            result["probability_model_path"] = str(args.fit_probability_model)
            result["probability_model_samples"] = model.get("samples", 0)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
