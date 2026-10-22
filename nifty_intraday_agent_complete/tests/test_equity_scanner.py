import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from accuracy_config import quality_settings as quality
from equity_scanner import build_equity_buy_signal


class EquityScannerTests(unittest.TestCase):
    def _frames(self) -> dict[str, pd.DataFrame]:
        one_minute_index = pd.date_range(
            "2026-01-05 09:15", periods=3, freq="min", tz="Asia/Kolkata"
        )
        five_minute_index = pd.date_range(
            "2026-01-05 09:00", periods=4, freq="5min", tz="Asia/Kolkata"
        )
        return {
            "1min": pd.DataFrame(
                {
                    "open": [99.0, 100.0, 101.0],
                    "high": [101.0, 102.0, 103.0],
                    "low": [98.0, 99.0, 100.0],
                    "close": [100.0, 101.0, 102.0],
                    "volume": [1000.0, 1200.0, 1300.0],
                    "atr_pct": [0.2, 0.25, 0.3],
                    "adx14": [18.0, 21.0, 24.0],
                    "plus_di14": [20.0, 22.0, 24.0],
                    "minus_di14": [18.0, 17.0, 16.0],
                    "relative_volume20": [1.1, 1.2, 1.3],
                    "body_pct": [0.4, 0.55, 0.7],
                },
                index=one_minute_index,
            ),
            "5min": pd.DataFrame(
                {
                    "open": [98.0, 99.0, 100.0, 101.0],
                    "high": [101.0, 102.0, 103.0, 104.0],
                    "low": [97.0, 98.0, 99.0, 100.0],
                    "close": [100.0, 101.0, 102.0, 103.0],
                    "volume": [5000.0, 5500.0, 6000.0, 6500.0],
                },
                index=five_minute_index,
            ),
        }

    def _analysis(self, relative_volume: float) -> dict[str, object]:
        return {
            "signal": "CALL",
            "atr": 1.0,
            "technical_score": 7,
            "context_score": 0,
            "total_score": 7,
            "relative_volume": relative_volume,
            "reason": "bullish technical setup",
            "market_regime": "TRENDING",
        }

    @patch("equity_scanner.detect_latest_candlestick_patterns")
    @patch("equity_scanner.build_signal")
    def test_equity_setup_requires_configured_relative_volume(
        self, build_signal_mock: MagicMock, patterns_mock: MagicMock
    ) -> None:
        patterns_mock.return_value = [{"name": "Hammer", "direction": "BULLISH"}]
        build_signal_mock.return_value = self._analysis(
            quality.relative_volume_threshold - 0.01
        )

        signal = build_equity_buy_signal("TEST", self._frames(), 102.0)

        self.assertIsNone(signal)

    @patch("equity_scanner.detect_latest_candlestick_patterns")
    @patch("equity_scanner.build_signal")
    def test_high_volume_equity_setup_passes_with_premarket_context(
        self, build_signal_mock: MagicMock, patterns_mock: MagicMock
    ) -> None:
        patterns_mock.return_value = [{"name": "Hammer", "direction": "BULLISH"}]
        context = {
            "directional_bias": "BULLISH_BIAS",
            "volatility_regime": "LOWER",
        }
        build_signal_mock.return_value = {
            **self._analysis(quality.relative_volume_threshold + 0.01),
            "premarket_context": context,
        }

        signal = build_equity_buy_signal(
            "TEST", self._frames(), 102.0, premarket_context=context
        )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["feature_snapshot"]["premarket_context"], context)
        self.assertEqual(
            build_signal_mock.call_args.kwargs["premarket_context"], context
        )
        snapshot = signal["feature_snapshot"]
        self.assertEqual(snapshot["version"], 2)
        self.assertEqual(snapshot["market_regime"], "TRENDING")
        self.assertEqual(snapshot["one_minute"]["adx14"], 21.0)
        self.assertEqual(snapshot["one_minute"]["relative_volume20"], 1.2)
        self.assertEqual(snapshot["one_minute"]["body_pct"], 0.55)

    @patch("equity_scanner.detect_latest_candlestick_patterns")
    @patch("equity_scanner.build_signal")
    def test_engine_wait_reason_is_preserved_in_scanner_diagnostics(
        self, build_signal_mock: MagicMock, patterns_mock: MagicMock
    ) -> None:
        patterns_mock.return_value = []
        build_signal_mock.return_value = {
            **self._analysis(quality.relative_volume_threshold + 0.1),
            "signal": "WAIT",
            "reason": "high-volatility market requires score >= 8",
        }
        diagnostics: dict[str, object] = {}

        signal = build_equity_buy_signal(
            "TEST", self._frames(), 102.0, diagnostics=diagnostics
        )

        self.assertIsNone(signal)
        self.assertEqual(
            diagnostics["reason"],
            "Signal engine returned WAIT: high-volatility market requires score >= 8",
        )


if __name__ == "__main__":
    unittest.main()