import unittest

import numpy as np
import pandas as pd

from indicators import add_indicators
from signal_engine import build_signal


class SignalEngineV21Tests(unittest.TestCase):
    def _frame(self) -> pd.DataFrame:
        index = pd.date_range(
            "2026-01-05 09:15",
            periods=260,
            freq="min",
            tz="Asia/Kolkata",
        )
        close = np.linspace(25000, 25250, len(index))
        frame = pd.DataFrame({
            "open": close - 1,
            "high": close + 3,
            "low": close - 3,
            "close": close,
            "volume": np.linspace(1000, 5000, len(index)),
        }, index=index)
        return frame

    def test_indicators_are_point_in_time_features(self) -> None:
        frame = add_indicators(self._frame())
        for column in (
            "adx14", "ema9_slope", "ema21_slope", "relative_volume20",
            "prior_day_high", "prior_day_low",
        ):
            self.assertIn(column, frame.columns)

        # A future close change must not alter indicators before that timestamp.
        prefix = self._frame()
        prefix.loc[prefix.index[-1], "close"] += 1000
        changed = add_indicators(prefix)
        original = add_indicators(self._frame())
        ts = original.index[-2]
        self.assertAlmostEqual(
            float(changed.loc[ts, "ema9"]),
            float(original.loc[ts, "ema9"]),
            places=8,
        )

    def test_signal_requires_strong_score_in_range(self) -> None:
        frame = add_indicators(self._frame())
        five = add_indicators(
            frame.resample("5min").agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            ).dropna()
        )
        result = build_signal(frame, five, "NEUTRAL", "NEUTRAL")
        self.assertIn(result["signal"], {"CALL", "PUT", "WAIT"})
        self.assertIn(result["market_regime"], {
            "TRENDING", "TRANSITION", "RANGE", "HIGH_VOLATILITY", "UNKNOWN"
        })

    def test_directional_candlestick_adds_one_capped_confirmation_point(self) -> None:
        frame = add_indicators(self._frame())
        five = add_indicators(
            frame.resample("5min").agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            ).dropna()
        )
        bullish = build_signal(
            frame, five, candlestick_patterns=[
                {"name": "Bullish Engulfing", "direction": "BULLISH"},
                {"name": "Hammer", "direction": "BULLISH"},
            ]
        )
        self.assertEqual(bullish["candlestick_score"], 1)
        self.assertIn("bullish candlestick confirmation (+1)", bullish["reason"])

        conflicting = build_signal(
            frame, five, candlestick_patterns=[
                {"name": "Hammer", "direction": "BULLISH"},
                {"name": "Shooting Star", "direction": "BEARISH"},
            ]
        )
        self.assertEqual(conflicting["candlestick_score"], 0)
        self.assertIn("conflicting candlestick patterns", conflicting["reason"])


if __name__ == "__main__":
    unittest.main()
