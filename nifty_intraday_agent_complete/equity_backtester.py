from __future__ import annotations

import argparse
import json
from datetime import date, time as datetime_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from accuracy_config import quality_settings as quality
from config import settings
import database
from equity_scanner import build_equity_buy_signal
from indicators import add_indicators, detect_candlestick_pattern_rows
from news_sources import equity_news_bias


MARKET_TIMEZONE = ZoneInfo(settings.market_timezone)
SESSION_OPEN = datetime_time(9, 15)
LAST_SESSION_MINUTE = datetime_time(15, 29)
TradeRow = dict[str, Any]


def _load_ohlcv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "timestamp" not in frame.columns:
        raise ValueError(f"{path} is missing the timestamp column")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(
        MARKET_TIMEZONE
    )
    frame = frame.set_index("timestamp").sort_index()
    frame.index = pd.DatetimeIndex(frame.index)
    frame = frame[~frame.index.duplicated(keep="last")]
    required = ["open", "high", "low", "close", "volume"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{path} is missing columns: {missing}")
    return frame[required].apply(pd.to_numeric, errors="coerce").dropna(
        subset=["open", "high", "low", "close"]
    )


def _resample_5m(minute: pd.DataFrame) -> pd.DataFrame:
    return minute.resample("5min").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna()


def resolve_on_bar(
    trade: dict[str, Any],
    bar_time: pd.Timestamp,
    bar: pd.Series[Any],
    expiry_minutes: int,
    round_trip_cost_bps: float,
) -> bool:
    signal_time = pd.Timestamp(trade["signal_time"])
    if signal_time.date() != bar_time.date() or (
        bar_time - signal_time > pd.to_timedelta(expiry_minutes, unit="min")
    ):
        trade["status"] = "EXPIRED"
        trade["exit_time"] = bar_time.isoformat()
        return True

    target_hit = float(bar["high"]) >= trade["target"]
    stop_hit = float(bar["low"]) <= trade["stop"]
    if target_hit and stop_hit:
        trade["status"] = "AMBIGUOUS"
        trade["exit_time"] = bar_time.isoformat()
        trade["exit_price"] = float(bar["close"])
        return True
    if target_hit:
        trade["status"] = "TARGET_HIT"
        trade["exit_time"] = bar_time.isoformat()
        trade["exit_price"] = float(trade["target"])
        trade["gross_r"] = abs(trade["target"] - trade["entry_price"]) / trade["risk_points"]
    elif stop_hit:
        trade["status"] = "STOP_HIT"
        trade["exit_time"] = bar_time.isoformat()
        trade["exit_price"] = float(trade["stop"])
        trade["gross_r"] = -1.0
    else:
        return False

    cost_points = trade["entry_price"] * round_trip_cost_bps / 10_000
    trade["net_r"] = trade["gross_r"] - cost_points / trade["risk_points"]
    return True


def calculate_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    wins = [trade for trade in trades if trade["status"] == "TARGET_HIT"]
    losses = [trade for trade in trades if trade["status"] == "STOP_HIT"]
    resolved = wins + losses
    ambiguous = sum(trade["status"] == "AMBIGUOUS" for trade in trades)
    expired = sum(trade["status"] == "EXPIRED" for trade in trades)
    gross_r = [float(trade["gross_r"]) for trade in resolved]
    net_r = [float(trade["net_r"]) for trade in resolved]
    gross_losses = abs(sum(value for value in gross_r if value < 0))
    net_losses = abs(sum(value for value in net_r if value < 0))
    return {
        "signals": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "ambiguous": ambiguous,
        "expired": expired,
        "resolved": len(resolved),
        "win_rate_percent": round(len(wins) / len(resolved) * 100, 2)
        if resolved else None,
        "average_r": round(sum(gross_r) / len(gross_r), 4) if gross_r else None,
        "profit_factor": round(
            sum(value for value in gross_r if value > 0) / gross_losses, 4
        ) if gross_losses else None,
        "net_average_r": round(sum(net_r) / len(net_r), 4) if net_r else None,
        "net_profit_factor": round(
            sum(value for value in net_r if value > 0) / net_losses, 4
        ) if net_losses else None,
    }


def flatten_threshold_trades(
    score_threshold_results: dict[str, Any],
) -> list[TradeRow]:
    return [
        {**trade, "score_threshold": int(score)}
        for score, result in score_threshold_results.items()
        for trade in result["trades"]
    ]


def has_bullish_confirmation(
    one_minute_patterns: list[dict[str, str]],
    five_minute_patterns: list[dict[str, str]],
) -> bool:
    patterns = one_minute_patterns + five_minute_patterns
    has_bullish = any(pattern["direction"] == "BULLISH" for pattern in patterns)
    has_bearish = any(pattern["direction"] == "BEARISH" for pattern in patterns)
    return has_bullish and not has_bearish


def _split_dates(dates: list[date], folds: int) -> list[list[date]]:
    base_size, remainder = divmod(len(dates), folds)
    chunks: list[list[date]] = []
    start = 0
    for index in range(folds):
        size = base_size + int(index < remainder)
        chunks.append(dates[start:start + size])
        start += size
    return chunks


def simulate_symbol(
    symbol: str,
    raw_minute: pd.DataFrame,
    score_thresholds: list[int],
    round_trip_cost_bps: float,
    fold_by_date: dict[date, int],
    news_items: list[dict[str, Any]] | None = None,
) -> dict[int, list[TradeRow]]:
    minute = add_indicators(raw_minute)
    five = add_indicators(_resample_5m(raw_minute))
    one_minute_pattern_rows = detect_candlestick_pattern_rows(minute)
    five_minute_pattern_rows = detect_candlestick_pattern_rows(five)
    last_bar_by_date: dict[date, pd.Timestamp] = {}
    for timestamp in pd.DatetimeIndex(minute.index):
        last_bar_by_date[timestamp.date()] = pd.Timestamp(timestamp)
    results: dict[int, list[TradeRow]] = {
        score: [] for score in score_thresholds
    }
    active_setup = {score: False for score in score_thresholds}
    open_trades: dict[int, list[TradeRow]] = {
        score: [] for score in score_thresholds
    }
    minimum_one_minute_history = max(40, quality.minimum_history_bars) + 1
    expiry_minutes = int(settings.signal_expiry_minutes)

    for index in range(1, len(minute)):
        current_time = pd.Timestamp(minute.index[index])
        previous_time = pd.Timestamp(minute.index[index - 1])
        previous_bar = minute.iloc[index - 1]
        date_changed = current_time.date() != previous_time.date()

        for score in score_thresholds:
            remaining: list[TradeRow] = []
            for trade in open_trades[score]:
                if not resolve_on_bar(
                    trade,
                    previous_time,
                    previous_bar,
                    expiry_minutes,
                    round_trip_cost_bps,
                ):
                    remaining.append(trade)
            open_trades[score] = remaining
            if date_changed:
                for trade in open_trades[score]:
                    trade["status"] = "EXPIRED"
                    trade["exit_time"] = previous_time.isoformat()
                open_trades[score] = []
                active_setup[score] = False

        if current_time.time() < SESSION_OPEN or current_time.time() > LAST_SESSION_MINUTE:
            continue

        one_minute = minute.iloc[max(0, index - minimum_one_minute_history + 1):index + 1]
        five_end = int(five.index.searchsorted(current_time.floor("5min"), side="left"))
        five_minute = five.iloc[max(0, five_end - 40):five_end]
        candidate = None
        if len(one_minute) >= minimum_one_minute_history and len(five_minute) >= 40:
            one_minute_patterns = one_minute_pattern_rows.get(
                pd.Timestamp(minute.index[index - 1]), []
            )
            five_minute_patterns = (
                five_minute_pattern_rows.get(pd.Timestamp(five.index[five_end - 1]), [])
                if five_end else []
            )
            signal_time = previous_time
            latest_resolvable_time = last_bar_by_date[current_time.date()]
            has_full_resolution_window = (
                signal_time + pd.to_timedelta(expiry_minutes, unit="min")
                <= latest_resolvable_time
            )
            if (
                has_full_resolution_window
                and has_bullish_confirmation(one_minute_patterns, five_minute_patterns)
            ):
                candidate = build_equity_buy_signal(
                    symbol,
                    {"1min": one_minute, "5min": five_minute},
                    float(minute.iloc[index]["open"]),
                    news_bias=equity_news_bias(
                        news_items or [],
                        symbol,
                        max_age_minutes=quality.news_max_age_minutes,
                        now=current_time.to_pydatetime(),
                        availability_based=True,
                    ),
                    one_minute_pattern_rows=one_minute_pattern_rows,
                    five_minute_pattern_rows=five_minute_pattern_rows,
                )

        for score in score_thresholds:
            if candidate is None:
                active_setup[score] = False
                continue
            if active_setup[score]:
                continue
            active_setup[score] = True
            if int(candidate["technical_score"]) < score:
                continue

            entry_price = float(candidate["entry_price"])
            stop_price = float(candidate["stop_loss"])
            target_price = float(candidate["target_price"])
            risk_points = abs(entry_price - stop_price)
            if risk_points <= 0:
                continue
            trade: TradeRow = {
                "symbol": symbol,
                "signal_time": candidate["signal_candle_time"],
                "entry_time": current_time.isoformat(),
                "entry_price": entry_price,
                "target": target_price,
                "stop": stop_price,
                "risk_points": risk_points,
                "technical_score": int(candidate["technical_score"]),
                "regime": candidate.get("market_regime", "UNKNOWN"),
                "news_bias": candidate.get("news_bias", "NEUTRAL"),
                "reason": candidate.get("reason", ""),
                "status": "OPEN",
                "exit_time": None,
                "exit_price": None,
                "gross_r": None,
                "net_r": None,
                "fold": fold_by_date.get(current_time.date()),
            }
            results[score].append(trade)
            open_trades[score].append(trade)

    if len(minute):
        final_time = pd.Timestamp(minute.index[-1])
        final_bar = minute.iloc[-1]
        for score in score_thresholds:
            for trade in open_trades[score]:
                if not resolve_on_bar(
                    trade,
                    final_time,
                    final_bar,
                    expiry_minutes,
                    round_trip_cost_bps,
                ):
                    trade["status"] = "EXPIRED"
                    trade["exit_time"] = final_time.isoformat()
            open_trades[score] = []

    return results


def run_equity_backtest(
    data_dir: str | Path,
    score_thresholds: list[int],
    folds: int = 4,
    round_trip_cost_bps: float = 0.0,
    progress: bool = False,
) -> dict[str, Any]:
    if folds < 2:
        raise ValueError("folds must be >= 2")
    if not score_thresholds:
        raise ValueError("score_thresholds cannot be empty")
    if round_trip_cost_bps < 0:
        raise ValueError("round_trip_cost_bps must be >= 0")
    if any(score < settings.min_total_score for score in score_thresholds):
        raise ValueError(f"score thresholds must be >= {settings.min_total_score}")
    score_thresholds = sorted(set(score_thresholds))

    paths = sorted(Path(data_dir).glob("*_1m.csv"))
    if not paths:
        raise FileNotFoundError(f"No *_1m.csv files found in {data_dir}")

    raw_frames = {path.stem.removesuffix("_1m"): _load_ohlcv(path) for path in paths}
    database.ensure_news_columns()
    news_items = database.list_news(limit=100_000)
    for item in news_items:
        fetched_at = item.get("fetched_at")
        if not fetched_at:
            continue
        available_time = pd.Timestamp(fetched_at)
        if available_time.tzinfo is None:
            available_time = available_time.tz_localize(MARKET_TIMEZONE)
        else:
            available_time = available_time.tz_convert(MARKET_TIMEZONE)
        item["available_epoch"] = available_time.timestamp()

    available_dates_by_symbol: dict[str, set[date]] = {}
    full_session_dates_by_symbol: dict[str, int] = {}
    all_dates: set[date] = set()
    for symbol, frame in raw_frames.items():
        daily_max: dict[date, datetime_time] = {}
        for timestamp in pd.DatetimeIndex(frame.index):
            day = timestamp.date()
            daily_max[day] = max(daily_max.get(day, timestamp.time()), timestamp.time())
        all_dates.update(daily_max)
        available_dates_by_symbol[symbol] = set(daily_max)
        full_session_dates_by_symbol[symbol] = sum(
            last_time >= LAST_SESSION_MINUTE for last_time in daily_max.values()
        )
    complete_date_sets = iter(available_dates_by_symbol.values())
    common_dates = set(next(complete_date_sets))
    for dates in complete_date_sets:
        common_dates.intersection_update(dates)
    available_dates = sorted(common_dates)
    dates_missing_from_any_symbol = sorted(all_dates - set(available_dates))
    warmup_days = 5
    validation_dates = available_dates[warmup_days:]
    if len(validation_dates) < folds:
        raise ValueError("Not enough complete trading dates for the requested folds")

    date_folds = _split_dates(validation_dates, folds)
    fold_by_date = {
        date: fold_index
        for fold_index, fold_dates in enumerate(date_folds, start=1)
        for date in fold_dates
    }
    results: dict[int, list[TradeRow]] = {
        score: [] for score in score_thresholds
    }
    volume_quality: dict[str, int] = {}
    for index, (symbol, frame) in enumerate(raw_frames.items(), start=1):
        volume_quality[symbol] = int(frame["volume"].fillna(0).eq(0).sum())
        if progress:
            print(
                f"[{index}/{len(raw_frames)}] Replaying {symbol} ({len(frame):,} bars)",
                flush=True,
            )
        simulated = simulate_symbol(
            symbol,
            frame,
            score_thresholds,
            round_trip_cost_bps,
            fold_by_date,
            news_items,
        )
        for score in score_thresholds:
            results[score].extend(simulated[score])

    summaries: dict[str, Any] = {}
    for score in score_thresholds:
        eligible = [trade for trade in results[score] if trade["fold"] is not None]
        summaries[str(score)] = {
            "aggregate": calculate_metrics(eligible),
            "folds": [
                {
                    "fold": fold_index,
                    "start_date": str(fold_dates[0]),
                    "end_date": str(fold_dates[-1]),
                    **calculate_metrics([
                        trade for trade in eligible if trade["fold"] == fold_index
                    ]),
                }
                for fold_index, fold_dates in enumerate(date_folds, start=1)
            ],
            "by_symbol": {
                symbol: calculate_metrics([
                    trade for trade in eligible if trade["symbol"] == symbol
                ])
                for symbol in raw_frames
            },
            "trades": eligible,
        }

    return {
        "source_directory": str(data_dir),
        "symbols": list(raw_frames),
        "rows_per_symbol": {symbol: len(frame) for symbol, frame in raw_frames.items()},
        "first_timestamp": min(frame.index.min() for frame in raw_frames.values()).isoformat(),
        "last_timestamp": max(frame.index.max() for frame in raw_frames.values()).isoformat(),
        "available_trading_dates": len(available_dates),
        "full_session_dates_by_symbol": full_session_dates_by_symbol,
        "warmup_trading_dates_excluded": warmup_days,
        "dates_missing_from_any_symbol": [
            str(day) for day in dates_missing_from_any_symbol
        ],
        "volume_zero_rows_by_symbol": volume_quality,
        "archived_news_items": len(news_items),
        "news_items_with_symbol_matches": sum(bool(item.get("symbols")) for item in news_items),
        "assumptions": {
            "entry": "open of the minute in which the live scanner would first see a tick",
            "news_bias": "timestamped archived company and macro headlines are used as of decision time; uncovered periods stay neutral",
            "same_bar_target_and_stop": "AMBIGUOUS and excluded from win rate",
            "expiry_minutes": settings.signal_expiry_minutes,
            "round_trip_cost_bps": round_trip_cost_bps,
            "trades_without_a_full_expiry_window_excluded": True,
            "score_filter_state": "a qualifying base setup is latched until the base scanner returns no signal",
        },
        "score_threshold_results": summaries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay the live equity scanner on downloaded 1-minute history"
    )
    parser.add_argument("--data-dir", default="data/equity_history_6m")
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument(
        "--scores",
        type=int,
        nargs="+",
        default=[
            settings.min_total_score,
            settings.min_total_score + 1,
            settings.min_total_score + 2,
        ],
    )
    parser.add_argument("--round-trip-cost-bps", type=float, default=0.0)
    parser.add_argument(
        "--output", default="data/equity_history_6m/equity_backtest_summary.json"
    )
    parser.add_argument(
        "--trades-output", default="data/equity_history_6m/equity_backtest_trades.csv"
    )
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()

    result = run_equity_backtest(
        args.data_dir,
        args.scores,
        folds=args.folds,
        round_trip_cost_bps=args.round_trip_cost_bps,
        progress=args.progress,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    trade_rows = flatten_threshold_trades(result["score_threshold_results"])
    trades_output = Path(args.trades_output)
    trades_output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(trade_rows).to_csv(trades_output, index=False)
    print(json.dumps({
        "summary": str(output),
        "trades": str(trades_output),
        "available_trading_dates": result["available_trading_dates"],
        "score_thresholds": {
            score: value["aggregate"]
            for score, value in result["score_threshold_results"].items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()