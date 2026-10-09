import tempfile
import unittest
from pathlib import Path

from market_context import load_constituent_weights, summarize_constituent_news, summarize_constituent_returns


class MarketContextTests(unittest.TestCase):
    def test_breadth_is_neutral_when_coverage_is_too_low(self):
        result = summarize_constituent_returns({"A": 0.2, "B": 0.1}, min_constituents=10)
        self.assertEqual(result["bias"], "NEUTRAL")
        self.assertEqual(result["constituents_seen"], 2)

    def test_bullish_bias_requires_broad_advancers_and_positive_mean(self):
        returns = {f"S{i}": (0.2 if i < 8 else -0.05) for i in range(10)}
        result = summarize_constituent_returns(returns, min_constituents=10)
        self.assertEqual(result["bias"], "BULLISH")
        self.assertEqual(result["weighting"], "equal_weighted_fallback")

    def test_conflicting_breadth_and_weighted_mean_stays_neutral(self):
        returns = {f"S{i}": (0.2 if i < 8 else -0.05) for i in range(10)}
        weights = {f"S{i}": (55 if i == 8 else 45 / 49) for i in range(50)}
        result = summarize_constituent_returns(returns, weights, min_constituents=10)
        self.assertEqual(result["bias"], "NEUTRAL")
        self.assertEqual(result["weighting"], "index_weighted")

    def test_weight_loader_reads_optional_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weights.csv"
            path.write_text("symbol,weight_pct\nTCS,4.1\nBAD,not-a-number\n", encoding="utf-8")
            self.assertEqual(load_constituent_weights(path), {"TCS": 4.1})

    def test_constituent_news_uses_index_weights_and_fresh_articles(self):
        now = 100000.0
        items = [
            {"title": "TCS raises guidance", "symbols": ["TCS"], "sentiment": 2, "published_epoch": now - 10},
            {"title": "Old Infosys downgrade", "symbols": ["INFY"], "sentiment": -3, "published_epoch": now - 7200},
        ]
        result = summarize_constituent_news(
            items, {"TCS": 10.0, "INFY": 90.0}, max_age_minutes=60, now_epoch=now, min_weight_constituents=2
        )
        self.assertEqual(result["bias"], "NEUTRAL")
        self.assertEqual(result["matched_headlines"], 1)
        self.assertEqual(result["weighting"], "index_weighted")

    def test_high_weight_constituent_news_has_more_index_impact(self):
        now = 100000.0
        tcs = [{"title": "TCS raises guidance", "symbols": ["TCS"], "sentiment": 2, "published_epoch": now - 10}]
        infy = [{"title": "Infosys raises guidance", "symbols": ["INFY"], "sentiment": 2, "published_epoch": now - 10}]
        tcs_result = summarize_constituent_news(tcs, {"TCS": 10.0, "INFY": 90.0}, now_epoch=now, min_weight_constituents=2)
        infy_result = summarize_constituent_news(infy, {"TCS": 10.0, "INFY": 90.0}, now_epoch=now, min_weight_constituents=2)
        self.assertEqual(tcs_result["bias"], "NEUTRAL")
        self.assertEqual(infy_result["bias"], "BULLISH")


if __name__ == "__main__":
    unittest.main()
