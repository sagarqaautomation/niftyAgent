import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from failure_guard import failure_guard_reason

IST = ZoneInfo("Asia/Kolkata")


def _snap(adx5=25.0, rsi1=50.0, rsi5=50.0, regime="TRENDING", close=100.0, vwap=100.0, atr=1.0):
    return {
        "market_regime": regime,
        "one_minute": {"rsi14": rsi1, "close": close, "vwap": vwap, "atr14": atr},
        "five_minute": {"adx14": adx5, "rsi14": rsi5},
    }


class FailureGuardTests(unittest.TestCase):
    def at(self, hour, minute):
        return datetime(2026, 10, 21, hour, minute, tzinfo=IST)

    def test_clean_setup_passes(self):
        self.assertIsNone(failure_guard_reason("PUT", self.at(11, 40), _snap(), []))

    def test_non_trending_regime_blocked(self):
        for regime in ("TRANSITION", "RANGE"):
            self.assertIn("Regime", failure_guard_reason("CALL", self.at(11, 0), _snap(regime=regime), []))

    def test_todays_failed_calls_are_blocked(self):
        # Signals #20 and #21 on 2026-10-22 were CALLs in a TRANSITION regime.
        snap = _snap(regime="TRANSITION", close=22738.1, vwap=22669.7, atr=17.2)
        self.assertIn("Regime", failure_guard_reason("CALL", self.at(10, 47), snap, []))

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

    def test_replay_blocks_failures_more_often_than_successes(self):
        db = Path(__file__).resolve().parents[1] / "data" / "nifty_agent.db"
        if not db.exists():
            self.skipTest("no local database")
        rows = sqlite3.connect(db).execute(
            "SELECT signal,status,feature_snapshot_json FROM signals "
            "WHERE status IN ('SUCCESS','FAILED') AND feature_snapshot_json IS NOT NULL"
        ).fetchall()
        counts = {"SUCCESS": [0, 0], "FAILED": [0, 0]}
        for direction, status, raw in rows:
            snap = json.loads(raw)
            when = datetime.fromisoformat(snap["signal_candle_time"])
            counts[status][0] += 1
            counts[status][1] += failure_guard_reason(direction, when, snap, []) is not None
        if min(counts["SUCCESS"][0], counts["FAILED"][0]) < 3:
            self.skipTest("not enough recorded signals")
        rate = {k: v[1] / v[0] for k, v in counts.items()}
        self.assertGreater(rate["FAILED"], rate["SUCCESS"])


if __name__ == "__main__":
    unittest.main()
