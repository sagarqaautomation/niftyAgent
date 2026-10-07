"""Deterministic historical evaluator for the NiftyAgent signal engine.

The evaluator intentionally enters on the next 1-minute candle open. Signals are
generated only from candles available at the decision timestamp, which avoids
look-ahead bias. If both target and stop are touched inside one OHLC bar, the
outcome is marked AMBIGUOUS rather than assuming an execution order.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import settings
from accuracy_config import quality_settings as quality
from indicators import add_indicators
from signal_engine import add_risk_levels, build_signal


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


def _resolve_trade(
    trade: Trade,
    future: pd.DataFrame,
) -> Trade:
    if future.empty:
        trade.status = "EXPIRED"
        return trade

    expiry = pd.Timestamp(trade.entry_time) + pd.Timedelta(minutes=settings.signal_expiry_minutes)
    future = future.loc[future.index <= expiry]

    for timestamp, bar in future.iterrows():
        target_hit = (
            bar["high"] >= trade.target if trade.direction == "CALL"
            else bar["low"] <= trade.target
        )
        stop_hit = (
            bar["low"] <= trade.stop if trade.direction == "CALL"
            else bar["high"] >= trade.stop
        )

        if target_hit and stop_hit:
            trade.status = "AMBIGUOUS"
            trade.exit_time = timestamp.isoformat()
            trade.exit_price = float(bar["close"])
            return trade
        if target_hit:
            trade.status = "SUCCESS"
            trade.exit_time = timestamp.isoformat()
            trade.exit_price = float(trade.target)
            trade.r_multiple = abs(trade.target - trade.entry) / abs(trade.entry - trade.stop)
            return trade
        if stop_hit:
            trade.status = "FAILED"
            trade.exit_time = timestamp.isoformat()
            trade.exit_price = float(trade.stop)
            trade.r_multiple = -1.0
            return trade

    trade.status = "EXPIRED"
    return trade


def run_backtest(frame: pd.DataFrame) -> dict[str, Any]:
    minute = frame.copy()
    one = add_indicators(minute)
    five = add_indicators(_resample_5m(minute))

    trades: list[Trade] = []
    last_signal_bar: pd.Timestamp | None = None
    last_direction: str | None = None
    reset_bars = 0

    # The signal at bar i is evaluated after bar i closes and entered at i+1 open.
    for i in range(max(60, quality.minimum_history_bars), len(one) - 1):
        decision_time = one.index[i]
        df1 = one.iloc[: i + 1]
        df5 = five.loc[five.index < decision_time.floor("5min")]

        if len(df5) < 40:
            continue

        signal = build_signal(df1, df5, "NEUTRAL", "NEUTRAL")
        direction = signal["signal"]

        if direction == "WAIT":
            reset_bars += 1
            if reset_bars >= quality.setup_reset_bars:
                last_direction = None
            continue

        reset_bars = 0
        if direction == last_direction:
            continue
        if last_signal_bar is not None and decision_time - last_signal_bar < pd.Timedelta(
            minutes=quality.signal_cooldown_minutes
        ):
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
        )
        trade = _resolve_trade(trade, one.iloc[i + 1 :])
        trades.append(trade)
        last_signal_bar = decision_time
        last_direction = direction

    resolved = [t for t in trades if t.status in {"SUCCESS", "FAILED"}]
    wins = sum(t.status == "SUCCESS" for t in resolved)
    losses = sum(t.status == "FAILED" for t in resolved)
    ambiguous = sum(t.status == "AMBIGUOUS" for t in trades)
    expired = sum(t.status == "EXPIRED" for t in trades)
    r_values = [t.r_multiple for t in resolved if t.r_multiple is not None]

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
            if resolved_regime else None
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
        "profit_factor": (
            round(sum(r for r in r_values if r > 0) / abs(sum(r for r in r_values if r < 0)), 4)
            if any(r < 0 for r in r_values) else None
        ),
        "by_regime": by_regime,
        "trades": [asdict(t) for t in trades],
    }


def walk_forward(frame: pd.DataFrame, folds: int = 5) -> list[dict[str, Any]]:
    if folds < 2:
        raise ValueError("folds must be >= 2")
    boundaries = np.linspace(0, len(frame), folds + 1, dtype=int)
    chunks = [frame.iloc[boundaries[i]:boundaries[i + 1]] for i in range(folds)]
    results: list[dict[str, Any]] = []
    for i in range(1, len(chunks)):
        train = pd.concat(chunks[:i])
        validation = chunks[i]
        # Current strategy has fixed parameters; the train set is retained in the
        # report so future parameter fitting can be added without changing the split.
        report = run_backtest(validation)
        results.append({
            "fold": i,
            "train_start": train.index[0].isoformat(),
            "train_end": train.index[-1].isoformat(),
            "validation_start": validation.index[0].isoformat(),
            "validation_end": validation.index[-1].isoformat(),
            "validation": {
                key: value for key, value in report.items() if key != "trades"
            },
        })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the NiftyAgent signal engine")
    parser.add_argument("csv", help="OHLCV CSV with timestamp,open,high,low,close,volume")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--walk-forward", action="store_true")
    args = parser.parse_args()

    frame = load_ohlcv_csv(args.csv)
    result = walk_forward(frame, args.folds) if args.walk_forward else run_backtest(frame)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
