import tempfile
import unittest
from pathlib import Path

from market_context import load_constituent_weights, summarize_constituent_returns


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
        weights = {f"S{i}": (90 if i == 8 else 1) for i in range(10)}
        result = summarize_constituent_returns(returns, weights, min_constituents=10)
        self.assertEqual(result["bias"], "NEUTRAL")
        self.assertEqual(result["weighting"], "index_weighted")

    def test_weight_loader_reads_optional_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weights.csv"
            path.write_text("symbol,weight_pct\nTCS,4.1\nBAD,not-a-number\n", encoding="utf-8")
            self.assertEqual(load_constituent_weights(path), {"TCS": 4.1})


if __name__ == "__main__":
    unittest.main()
