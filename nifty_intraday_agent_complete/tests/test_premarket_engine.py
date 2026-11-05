import unittest

from premarket_engine import build_premarket_context


class PreMarketContextTests(unittest.TestCase):
    def test_insufficient_data_does_not_create_directional_signal(self):
        result = build_premarket_context(gift_nifty_change_pct=0.5, india_vix=13)
        self.assertEqual(result["directional_bias"], "INSUFFICIENT_DATA")
        self.assertEqual(result["trade_instruction"], "NONE")

    def test_positive_context_is_bullish_bias_not_trade(self):
        result = build_premarket_context(
            gift_nifty_change_pct=0.8,
            global_sentiment=0.7,
            advance_decline_ratio=2.0,
            news_sentiment=0.3,
            india_vix=14,
            observed_at="2026-10-14T08:55:00+05:30",
        )
        self.assertEqual(result["directional_bias"], "BULLISH_BIAS")
        self.assertEqual(result["volatility_regime"], "LOWER")
        self.assertEqual(result["trade_instruction"], "NONE")

    def test_vix_is_risk_context_not_direction(self):
        low = build_premarket_context(
            gift_nifty_change_pct=0.3, global_sentiment=0.2, india_vix=12
        )
        high = build_premarket_context(
            gift_nifty_change_pct=0.3, global_sentiment=0.2, india_vix=25
        )
        self.assertEqual(low["directional_score"], high["directional_score"])
        self.assertEqual(high["volatility_regime"], "HIGH")
        self.assertIn("HIGH_VOLATILITY", high["risk_flags"])

    def test_missing_inputs_are_not_fabricated(self):
        result = build_premarket_context()
        self.assertEqual(result["directional_bias"], "INSUFFICIENT_DATA")
        self.assertEqual(result["available_components"], 0)
        self.assertIn("INDIA_VIX_UNAVAILABLE", result["risk_flags"])

    def test_invalid_normalized_sentiment_is_ignored(self):
        result = build_premarket_context(
            global_sentiment=4, news_sentiment=float("nan")
        )
        self.assertEqual(result["available_components"], 0)


if __name__ == "__main__":
    unittest.main()
