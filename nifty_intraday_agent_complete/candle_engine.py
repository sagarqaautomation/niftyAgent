from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import pandas as pd

from config import settings
from indicators import add_indicators


class CandleEngine:
    """Incrementally builds minute candles from ticks and maintains 1m/5m frames.

    The volume passed to add_tick must be incremental traded volume since the
    previous tick, not Kite's cumulative volume_traded. The live adapter
    computes that delta before calling this class.
    """

    _OHLCV = ("open", "high", "low", "close", "volume")

    def __init__(self, max_rows: int = 500) -> None:
        empty_index = pd.DatetimeIndex(
            [], tz=settings.market_timezone, name="timestamp"
        )
        self.max_rows = max_rows
        self.history = pd.DataFrame(
            {
                column: pd.Series(index=empty_index, dtype="float64")
                for column in self._OHLCV
            },
            index=empty_index,
        )
        self.frames: dict[str, pd.DataFrame] = {
            "1min": pd.DataFrame(),
            "5min": pd.DataFrame(),
        }
        self._active_minute: pd.Timestamp | None = None
        self._active_bar: dict[str, float] | None = None
        self._last_tick_timestamp: pd.Timestamp | None = None

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
            history.index < current_minute, list(self._OHLCV)
        ].sort_index()
        self.history = self.history[
            ~self.history.index.duplicated(keep="last")
        ].tail(self.max_rows)
        self._active_minute = None
        self._active_bar = None
        self._last_tick_timestamp = None
        self._update_frames(self.history)
        return self.frames

    def _update_frames(self, minute: pd.DataFrame) -> None:
        if minute.empty:
            self.frames = {"1min": pd.DataFrame(), "5min": pd.DataFrame()}
            return

        minute = minute.sort_index()
        minute = minute[~minute.index.duplicated(keep="last")].tail(self.max_rows)
        self.frames["1min"] = add_indicators(minute)
        five = minute.resample("5min").agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        ).dropna(subset=["open", "high", "low", "close"])
        self.frames["5min"] = add_indicators(five.tail(self.max_rows))

    def _frame_with_active_bar(self) -> pd.DataFrame:
        minute = self.history
        if self._active_minute is not None and self._active_bar is not None:
            active = pd.DataFrame(
                [self._active_bar],
                index=pd.DatetimeIndex([self._active_minute], name=minute.index.name),
            )
            minute = pd.concat([minute, active])
        minute = minute.sort_index()
        minute = minute[~minute.index.duplicated(keep="last")].tail(self.max_rows)
        return minute

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

        # Do not let delayed ticks overwrite the close of a candle that has
        # already advanced, even when the delayed tick is in the same minute.
        if (
            self._last_tick_timestamp is not None
            and timestamp < self._last_tick_timestamp
        ):
            return self.frames if not self.frames["1min"].empty else None

        minute_start = timestamp.floor("min")
        tick_price = float(price)
        tick_volume = max(0.0, float(volume or 0.0))

        if self._active_minute is None:
            self._active_minute = minute_start
            self._active_bar = {
                "open": tick_price,
                "high": tick_price,
                "low": tick_price,
                "close": tick_price,
                "volume": tick_volume,
            }
        elif minute_start < self._active_minute:
            return self.frames if not self.frames["1min"].empty else None
        elif minute_start == self._active_minute:
            assert self._active_bar is not None
            self._active_bar["high"] = max(self._active_bar["high"], tick_price)
            self._active_bar["low"] = min(self._active_bar["low"], tick_price)
            self._active_bar["close"] = tick_price
            self._active_bar["volume"] += tick_volume
        else:
            # Close the prior active candle and retain aggregated bars only.
            assert self._active_bar is not None
            closed = pd.DataFrame(
                [self._active_bar],
                index=pd.DatetimeIndex([self._active_minute], name=self.history.index.name),
            )
            self.history = pd.concat([self.history, closed]).sort_index()
            self.history = self.history[
                ~self.history.index.duplicated(keep="last")
            ].tail(self.max_rows)
            self._active_minute = minute_start
            self._active_bar = {
                "open": tick_price,
                "high": tick_price,
                "low": tick_price,
                "close": tick_price,
                "volume": tick_volume,
            }

        self._last_tick_timestamp = timestamp
        self._update_frames(self._frame_with_active_bar())
        return self.frames
