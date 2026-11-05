import unittest

from equity_live_learning import _record


class EquityLiveLearningTests(unittest.TestCase):
    def test_legacy_snapshot_without_training_features_is_excluded(self) -> None:
        snapshot = {
            "version": 1,
            "one_minute": {"close": 100.0, "vwap": 99.0},
        }

        record = _record(
            1, "2026-10-01T10:00:00+05:30", "TEST", "TARGET_HIT", snapshot
        )

        self.assertIsNone(record)

    def test_version_two_snapshot_provides_learned_features(self) -> None:
        snapshot = {
            "version": 2,
            "signal_candle_time": "2026-10-01T10:00:00+05:30",
            "market_regime": "TRENDING",
            "one_minute": {
                "close": 101.0,
                "vwap": 100.0,
                "adx14": 28.0,
                "rsi14": 61.0,
                "relative_volume20": 1.5,
                "body_pct": 0.7,
                "opening_range_high": 100.5,
            },
            "one_minute_patterns": [{"name": "Hammer"}],
            "five_minute_patterns": [],
        }

        record = _record(
            2, "2026-10-01T10:00:00+05:30", "TEST", "TARGET_HIT", snapshot
        )

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["regime"], "TRENDING")
        self.assertEqual(record["adx"], 28.0)
        self.assertEqual(record["relative_volume"], 1.5)
        self.assertEqual(record["candle_strength"], 0.7)
        self.assertTrue(record["pattern_hammer"])
        self.assertEqual(record["structure"], "BREAKOUT_UP")


if __name__ == "__main__":
    unittest.main()
