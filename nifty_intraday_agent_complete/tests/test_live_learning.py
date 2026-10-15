import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from live_learning import build_training_record, load_resolved_records, maybe_train_candidate, train_candidate


class LiveLearningTests(unittest.TestCase):
    def _snapshot(self, index: int = 0) -> dict:
        return {
            "version": 3,
            "signal_candle_time": f"2026-01-01T09:{index % 60:02d}:00+05:30",
            "market_regime": "TREND",
            "one_minute": {
                "close": 22000 + index,
                "vwap": 21990 + index,
                "adx14": 24.0,
                "rsi14": 58.0 if index % 2 else 42.0,
                "relative_volume20": 1.4,
                "body_pct": 0.72,
                "close_location": 0.8,
                "opening_range_high": 21950,
                "opening_range_low": 21850,
            },
            "five_minute": {},
        }

    def _database(self, path: Path, count: int = 120) -> None:
        with sqlite3.connect(path) as conn:
            conn.execute("""
                CREATE TABLE signals (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    feature_snapshot_json TEXT,
                    signal_instrument TEXT
                )
            """)
            rows = []
            for i in range(count):
                status = "SUCCESS" if i % 2 == 0 else "FAILED"
                rows.append((
                    i + 1,
                    f"2026-01-01T{9 + (i // 60):02d}:{i % 60:02d}:00+00:00",
                    "CALL" if i % 3 else "PUT",
                    status,
                    json.dumps(self._snapshot(i)),
                    "NIFTY spot index",
                ))
            conn.executemany("INSERT INTO signals VALUES (?,?,?,?,?,?)", rows)

    def test_build_training_record_uses_point_in_time_features(self) -> None:
        record = build_training_record(
            7, "2026-01-01T04:00:00+00:00", "CALL", "SUCCESS", self._snapshot(5)
        )
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["direction"], "CALL")
        self.assertEqual(record["status"], "SUCCESS")
        self.assertEqual(record["regime"], "TREND")
        self.assertEqual(record["structure"], "BREAKOUT_UP")
        self.assertAlmostEqual(record["candle_strength"], 0.72)

    def test_unresolved_or_wait_signals_are_not_training_labels(self) -> None:
        self.assertIsNone(build_training_record(1, "now", "WAIT", "SUCCESS", self._snapshot()))
        self.assertIsNone(build_training_record(1, "now", "CALL", "AMBIGUOUS", self._snapshot()))

    def test_trains_research_candidate_with_chronological_holdout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "nifty.db"
            self._database(db)
            records = load_resolved_records(db)
            self.assertEqual(len(records), 120)
            report = train_candidate(records, root / "out", min_samples=100)
            self.assertIn(report["status"], {"REVIEW_ONLY", "NO_VALIDATED_IMPROVEMENT"})
            self.assertEqual(report["train_samples"], 96)
            self.assertEqual(report["holdout_samples"], 24)
            self.assertFalse(report["active_model_changed"])
            self.assertTrue((root / "out" / "research_candidate_model.json").exists())

    def test_automatic_training_waits_for_new_resolved_samples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "nifty.db"
            self._database(db)
            out = root / "out"
            first = maybe_train_candidate(db, out, min_samples=100, retrain_after_new=20)
            self.assertIsNotNone(first)
            self.assertTrue((out / "live_learning_report.json").exists())
            second = maybe_train_candidate(db, out, min_samples=100, retrain_after_new=20)
            self.assertIsNone(second)


if __name__ == "__main__":
    unittest.main()
