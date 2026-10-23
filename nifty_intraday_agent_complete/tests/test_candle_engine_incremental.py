import unittest
from unittest.mock import patch

import pandas as pd

from candle_engine import CandleEngine


TZ = "Asia/Kolkata"


class CandleEngineIncrementalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.indicator_patch = patch(
            "candle_engine.add_indicators",
            side_effect=lambda frame: frame.copy(),
        )
        self.indicator_patch.start()
        self.addCleanup(self.indicator_patch.stop)
        self.engine = CandleEngine(max_rows=20)

    def test_aggregates_ohlcv_and_closes_previous_minute(self) -> None:
        self.engine.add_tick("2026-01-05 09:15:01+05:30", 100, 0)
        self.engine.add_tick("2026-01-05 09:15:20+05:30", 103, 5)
        frames = self.engine.add_tick("2026-01-05 09:15:40+05:30", 99, 3)
        self.assertIsNotNone(frames)

        frames = self.engine.add_tick("2026-01-05 09:16:00+05:30", 101, 2)
        assert frames is not None
        prior_time = pd.Timestamp("2026-01-05 09:15:00", tz=TZ)
        current_time = pd.Timestamp("2026-01-05 09:16:00", tz=TZ)

        prior = frames["1min"].loc[prior_time]
        self.assertEqual(float(prior["open"]), 100)
        self.assertEqual(float(prior["high"]), 103)
        self.assertEqual(float(prior["low"]), 99)
        self.assertEqual(float(prior["close"]), 99)
        self.assertEqual(float(prior["volume"]), 8)

        current = frames["1min"].loc[current_time]
        self.assertEqual(float(current["open"]), 101)
        self.assertEqual(float(current["close"]), 101)
        self.assertEqual(float(current["volume"]), 2)

    def test_ignores_late_tick_from_a_previous_minute(self) -> None:
        self.engine.add_tick("2026-01-05 09:15:10+05:30", 100, 1)
        frames = self.engine.add_tick("2026-01-05 09:16:10+05:30", 102, 2)
        assert frames is not None

        returned = self.engine.add_tick("2026-01-05 09:15:50+05:30", 90, 100)
        assert returned is not None
        current_time = pd.Timestamp("2026-01-05 09:16:00", tz=TZ)
        current = returned["1min"].loc[current_time]
        self.assertEqual(float(current["close"]), 102)
        self.assertEqual(float(current["volume"]), 2)

        prior_time = pd.Timestamp("2026-01-05 09:15:00", tz=TZ)
        prior = returned["1min"].loc[prior_time]
        self.assertEqual(float(prior["close"]), 100)
        self.assertEqual(float(prior["volume"]), 1)

    def test_does_not_keep_a_session_sized_tick_buffer(self) -> None:
        self.engine.add_tick("2026-01-05 09:15:10+05:30", 100, 1)
        self.assertFalse(hasattr(self.engine, "ticks"))


if __name__ == "__main__":
    unittest.main()
