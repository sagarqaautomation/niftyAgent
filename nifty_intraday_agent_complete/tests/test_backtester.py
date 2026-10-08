import unittest
from typing import Any
from unittest.mock import patch

import pandas as pd

import backtester


class BacktesterTests(unittest.TestCase):
    def test_round_trip_cost_reduces_net_r(self) -> None:
        winning_r = backtester.net_r_multiple(1.5, 2.0, 40.0)
        losing_r = backtester.net_r_multiple(-1.0, 2.0, 40.0)
        if winning_r is None or losing_r is None:
            self.fail("resolved trades must have net R values")
        self.assertAlmostEqual(winning_r, 1.45)
        self.assertAlmostEqual(losing_r, -1.05)

    def test_score_sweep_reports_each_threshold_for_each_fold(self) -> None:
        frame = pd.DataFrame(
            index=pd.date_range(
                "2026-10-05 09:00", periods=12, freq="min", tz="Asia/Kolkata"
            )
        )
        report: dict[str, Any] = {
            "signals": 1,
            "wins": 1,
            "losses": 0,
            "resolved": 1,
            "win_rate_percent": 100.0,
            "average_r": 1.0,
            "profit_factor": None,
            "net_average_r": 0.9,
            "net_profit_factor": None,
            "trades": [],
        }
        with patch.object(backtester, "run_backtest", return_value=report) as run:
            results = backtester.walk_forward(
                frame,
                folds=4,
                score_thresholds=[9, 7, 8, 8],
                round_trip_cost_points=1.0,
            )

        self.assertEqual(len(results), 3)
        for fold in results:
            self.assertEqual(
                list(fold["score_threshold_results"]), ["7", "8", "9"]
            )
        self.assertEqual(run.call_count, 9)
        self.assertEqual(run.call_args.kwargs["round_trip_cost_points"], 1.0)

    def test_rejected_score_does_not_reset_direction_state(self) -> None:
        index = pd.date_range(
            "2026-10-05 09:00", periods=63, freq="min", tz="Asia/Kolkata"
        )
        minute = pd.DataFrame({
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1.0,
            "atr14": 1.0,
            "vwap": 100.0,
            "body_pct": 0.7,
            "rsi14": 50.0,
            "opening_range_high": float("nan"),
            "opening_range_low": float("nan"),
        }, index=index)
        five = pd.DataFrame(
            index=pd.date_range(
                "2026-10-05 06:40", periods=40, freq="5min", tz="Asia/Kolkata"
            )
        )

        def score_signal(score: int) -> dict[str, Any]:
            return {
                "signal": "CALL",
                "technical_score": score,
                "market_regime": "TRENDING",
                "setup_key": "CALL|TRENDING",
                "adx": 25.0,
                "relative_volume": 1.0,
            }

        def add_test_risk_levels(signal: dict[str, Any]) -> dict[str, Any]:
            signal["target_price"] = 102.0
            signal["stop_loss"] = 99.0
            return signal

        results: dict[int, dict[str, Any]] = {}
        for threshold in (7, 8):
            with (
                patch.object(backtester, "add_indicators", side_effect=[minute, five]),
                patch.object(backtester, "_resample_5m", return_value=five),
                patch.object(
                    backtester,
                    "build_signal",
                    side_effect=[score_signal(7), score_signal(8)],
                ),
                patch.object(
                    backtester,
                    "add_risk_levels",
                    side_effect=add_test_risk_levels,
                ),
            ):
                results[threshold] = backtester.run_backtest(
                    minute,
                    minimum_technical_score=threshold,
                )

        self.assertEqual(results[7]["signals"], 1)
        self.assertEqual(results[8]["signals"], 0)


if __name__ == "__main__":
    unittest.main()