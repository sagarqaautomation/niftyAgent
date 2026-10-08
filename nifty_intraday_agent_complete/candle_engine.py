from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import pandas as pd
from config import settings
from indicators import add_indicators

class CandleEngine:
    """Converts ticks into minute candles and maintains 1m/5m DataFrames."""

    def __init__(self, max_rows: int = 500) -> None:
        self.ticks: list[dict[str, Any]] = []
        self.max_rows = max_rows
        self.history = pd.DataFrame()
        self.frames: dict[str, pd.DataFrame] = {
            "1min": pd.DataFrame(),
            "5min": pd.DataFrame(),
        }

    def load_history(
        self, candles: Sequence[Mapping[str, Any]]
    ) -> dict[str, pd.DataFrame]:
        if not candles:
            return self.frames

        history = pd.DataFrame(candles).rename(columns={"date": "timestamp"})
        history["timestamp"] = pd.to_datetime(history["timestamp"])
        history = history.set_index("timestamp")
        history_index = pd.DatetimeIndex(history.index)
        if history_index.tz is None:
            history.index = history_index.tz_localize(settings.market_timezone)
        else:
            history.index = history_index.tz_convert(settings.market_timezone)

        if "volume" not in history:
            history["volume"] = 0
        current_minute = pd.Timestamp.now(tz=settings.market_timezone).floor("min")
        self.history = history.loc[
            history.index < current_minute, ["open", "high", "low", "close", "volume"]
        ].sort_index()
        self.history = self.history[~self.history.index.duplicated(keep="last")].tail(self.max_rows)
        self._update_frames(self.history)
        return self.frames

    def _update_frames(self, minute: pd.DataFrame) -> None:
        if minute.empty:
            self.frames = {"1min": pd.DataFrame(), "5min": pd.DataFrame()}
            return

        self.frames["1min"] = add_indicators(minute.tail(self.max_rows))
        five = minute.resample("5min").agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum")
        ).dropna()
        self.frames["5min"] = add_indicators(five.tail(self.max_rows))

    def add_tick(
        self,
        timestamp: pd.Timestamp | datetime | str,
        price: float,
        volume: float = 0,
    ) -> dict[str, pd.DataFrame] | None:
        timestamp = pd.Timestamp(timestamp)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(settings.market_timezone)
        else:
            timestamp = timestamp.tz_convert(settings.market_timezone)

        self.ticks.append({
            "timestamp": timestamp,
            "price": float(price),
            "volume": float(volume or 0)
        })
        self.ticks = self.ticks[-100000:]

        raw = pd.DataFrame(self.ticks)
        if raw.empty:
            return None

        raw = raw.set_index("timestamp")
        minute = raw.resample("1min").agg(
            open=("price","first"),
            high=("price","max"),
            low=("price","min"),
            close=("price","last"),
            volume=("volume","sum")
        ).dropna()

        minute = pd.concat([self.history, minute]).sort_index()
        minute = minute[~minute.index.duplicated(keep="last")].tail(self.max_rows)
        self._update_frames(minute)

        return self.frames
