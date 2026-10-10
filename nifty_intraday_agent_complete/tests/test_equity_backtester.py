import unittest
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

import equity_backtester
from equity_backtester import (
    calculate_metrics,
    flatten_threshold_trades,
    has_bullish_confirmation,
    resolve_on_bar,
    simulate_symbol,
)


class EquityBacktesterTests(unittest.TestCase):
    def test_flattened_trade_rows_keep_score_threshold(self) -> None:
        rows = flatten_threshold_trades({
            "7": {"trades": [{"symbol": "ABC", "status": "TARGET_HIT"}]},
            "9": {"trades": [{"symbol": "XYZ", "status": "STOP_HIT"}]},
        })

        self.assertEqual(
            [(row["symbol"], row["score_threshold"]) for row in rows],
            [("ABC", 7), ("XYZ", 9)],
        )

    def test_pattern_prefilter_matches_live_confirmation_rule(self) -> None:
        bullish = [{"name": "Hammer", "direction": "BULLISH"}]
        bearish = [{"name": "Shooting Star", "direction": "BEARISH"}]
        self.assertTrue(has_bullish_confirmation(bullish, []))
        self.assertFalse(has_bullish_confirmation([], bearish))
        self.assertFalse(has_bullish_confirmation(bullish, bearish))

    def _trade(self) -> dict[str, Any]:
        return {
            "signal_time": "2026-10-08T10:00:00+05:30",
            "entry_price": 100.0,
            "target": 103.0,
            "stop": 98.0,
            "risk_points": 2.0,
            "status": "OPEN",
        }

    def test_target_resolution_applies_round_trip_cost(self) -> None:
        trade = self._trade()
        bar = pd.Series({"high": 103.0, "low": 99.0, "close": 102.0})

        resolved = resolve_on_bar(
            trade,
            pd.Timestamp("2026-10-08T10:05:00+05:30"),
            bar,
            expiry_minutes=30,
            round_trip_cost_bps=10.0,
        )

        self.assertTrue(resolved)
        self.assertEqual(trade["status"], "TARGET_HIT")
        self.assertAlmostEqual(trade["gross_r"], 1.5)
        self.assertAlmostEqual(trade["net_r"], 1.45)

    def test_both_levels_in_one_bar_are_ambiguous(self) -> None:
        trade = self._trade()
        bar = pd.Series({"high": 104.0, "low": 97.0, "close": 100.0})

        resolved = resolve_on_bar(
            trade,
            pd.Timestamp("2026-10-08T10:05:00+05:30"),
            bar,
            expiry_minutes=30,
            round_trip_cost_bps=0.0,
        )

        self.assertTrue(resolved)
        self.assertEqual(trade["status"], "AMBIGUOUS")
        self.assertIsNone(trade.get("gross_r"))

    def test_signal_expires_after_configured_window(self) -> None:
        trade = self._trade()
        bar = pd.Series({"high": 101.0, "low": 99.0, "close": 100.0})

        resolved = resolve_on_bar(
            trade,
            pd.Timestamp("2026-10-08T10:31:00+05:30"),
            bar,
            expiry_minutes=30,
            round_trip_cost_bps=0.0,
        )

        self.assertTrue(resolved)
        self.assertEqual(trade["status"], "EXPIRED")

    def test_metrics_exclude_ambiguous_and_expired_from_win_rate(self) -> None:
        trades: list[dict[str, Any]] = [
            {"status": "TARGET_HIT", "gross_r": 1.5, "net_r": 1.4},
            {"status": "STOP_HIT", "gross_r": -1.0, "net_r": -1.1},
            {"status": "AMBIGUOUS"},
            {"status": "EXPIRED"},
        ]

        metrics = calculate_metrics(trades)

        self.assertEqual(metrics["resolved"], 2)
        self.assertEqual(metrics["win_rate_percent"], 50.0)
        self.assertEqual(metrics["ambiguous"], 1)
        self.assertEqual(metrics["expired"], 1)

    def test_replay_keeps_one_forming_bar_outside_required_history(self) -> None:
        timezone = ZoneInfo("Asia/Kolkata")
        minute_index = pd.date_range(
            "2026-10-08 09:15", periods=100, freq="min", tz=timezone
        )
        five_index = pd.date_range(
            "2026-10-08 06:55", periods=40, freq="5min", tz=timezone
        )
        values = {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}
        minute = pd.DataFrame(values, index=minute_index)
        five = pd.DataFrame(values, index=five_index)
        one_minute_patterns = {
            minute_index[59]: [{"name": "Test Bullish Pattern", "direction": "BULLISH"}]
        }

        with (
            patch.object(equity_backtester, "add_indicators", side_effect=[minute, five]),
            patch.object(equity_backtester, "_resample_5m", return_value=five),
            patch.object(
                equity_backtester,
                "detect_candlestick_pattern_rows",
                side_effect=[one_minute_patterns, {}],
            ),
            patch.object(equity_backtester, "build_equity_buy_signal", return_value=None) as build,
            patch.object(
                equity_backtester,
                "equity_news_bias",
                return_value="BULLISH",
            ) as news_bias,
        ):
            simulate_symbol(
                "TEST",
                minute,
                [7],
                0.0,
                {},
                [{"title": "time-tagged test headline"}],
            )

        self.assertGreater(build.call_count, 0)
        self.assertGreater(news_bias.call_count, 0)
        self.assertIsNotNone(news_bias.call_args.kwargs["now"])
        self.assertTrue(news_bias.call_args.kwargs["availability_based"])


if __name__ == "__main__":
    unittest.main()