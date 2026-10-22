import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from failure_guard import failure_guard_reason

IST = ZoneInfo("Asia/Kolkata")


def _snap(adx5=25.0, rsi1=50.0, rsi5=50.0):
    return {
        "one_minute": {"rsi14": rsi1},
        "five_minute": {"adx14": adx5, "rsi14": rsi5},
    }


class FailureGuardTests(unittest.TestCase):
    def at(self, hour, minute):
        return datetime(2026, 10, 21, hour, minute, tzinfo=IST)

    def test_clean_setup_passes(self):
        self.assertIsNone(failure_guard_reason("PUT", self.at(11, 40), _snap(), []))

    def test_opening_noise_blocked(self):
        self.assertIn("Opening", failure_guard_reason("PUT", self.at(9, 32), _snap(), []))

    def test_exhaustion_blocked(self):
        self.assertIn("exhaustion", failure_guard_reason("PUT", self.at(12, 44), _snap(adx5=43.7), []))

    def test_overbought_call_blocked(self):
        self.assertIn("Overbought", failure_guard_reason("CALL", self.at(11, 0), _snap(rsi1=83.6), []))

    def test_oversold_put_blocked(self):
        self.assertIn("Oversold", failure_guard_reason("PUT", self.at(11, 0), _snap(rsi5=13.2), []))

    def test_loss_streak_blocks_same_direction_only(self):
        now = datetime.now(timezone.utc)
        failed = [{"signal": "PUT", "status": "FAILED", "created_at": (now - timedelta(minutes=10)).isoformat()}]
        self.assertIn("Loss-streak", failure_guard_reason("PUT", self.at(11, 40), _snap(), failed, now=now))
        self.assertIsNone(failure_guard_reason("CALL", self.at(11, 40), _snap(), failed, now=now))
        old = [{"signal": "PUT", "status": "FAILED", "created_at": (now - timedelta(minutes=45)).isoformat()}]
        self.assertIsNone(failure_guard_reason("PUT", self.at(11, 40), _snap(), old, now=now))

    def test_replay_of_recorded_signals_blocks_failures_not_successes(self):
        db = Path(__file__).resolve().parents[1] / "data" / "nifty_agent.db"
        if not db.exists():
            self.skipTest("no local database")
        rows = sqlite3.connect(db).execute(
            "SELECT signal,status,feature_snapshot_json FROM signals "
            "WHERE status IN ('SUCCESS','FAILED') AND feature_snapshot_json IS NOT NULL"
        ).fetchall()
        successes_blocked = 0
        for direction, status, raw in rows:
            snap = json.loads(raw)
            when = datetime.fromisoformat(snap["signal_candle_time"])
            blocked = failure_guard_reason(direction, when, snap, []) is not None
            if status == "SUCCESS" and blocked:
                successes_blocked += 1
        self.assertEqual(successes_blocked, 0)


if __name__ == "__main__":
    unittest.main()
