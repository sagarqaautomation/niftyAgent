import time
import unittest

from equity_research import assess_analysts, assess_fundamentals, assess_news, build_research, select_candidates


def _item(title: str, sentiment: int, categories: list[str]) -> dict[str, object]:
    return {
        "title": title, "summary": "", "source": "T", "sentiment": sentiment,
        "categories": categories, "published_epoch": time.time() - 600, "symbols": [],
    }


class EquityResearchTests(unittest.TestCase):
    def test_positive_results_news_scores_positive(self) -> None:
        items = [_item("Infosys net profit rises, beats estimates", 3, ["EARNINGS"])]
        self.assertGreater(assess_news(items, "INFY", ["Infosys"])["score"], 0)

    def test_unrelated_news_ignored(self) -> None:
        items = [_item("TCS order win", 3, ["COMPANY_ANNOUNCEMENT"])]
        self.assertEqual(assess_news(items, "INFY", ["Infosys"])["score"], 0)

    def test_analyst_target_upside_extracted(self) -> None:
        items = [_item("Infosys: Brokerage upgrades to buy, target price Rs 1,650", 2, ["ANALYST_ACTION"])]
        result = assess_analysts(items, "INFY", ["Infosys"], 1500.0)
        self.assertEqual(result["targets"][0]["target"], 1650.0)
        self.assertEqual(result["score"], 2)

    def test_downgrade_is_negative(self) -> None:
        items = [_item("Infosys downgrade by brokerage, target price Rs 1,400", 0, ["ANALYST_ACTION"])]
        self.assertLess(assess_analysts(items, "INFY", ["Infosys"], 1500.0)["score"], 0)

    def test_fundamentals(self) -> None:
        good = {"revenueGrowth": 0.2, "earningsGrowth": 0.2, "profitMargins": 0.15,
                "debtToEquity": 20, "freeCashflow": 5, "pegRatio": 1.0}
        bad = {"revenueGrowth": -0.1, "earningsGrowth": -0.2, "profitMargins": -0.1,
               "debtToEquity": 300, "freeCashflow": -5}
        self.assertGreater(assess_fundamentals(good)["score"], 0)
        self.assertLess(assess_fundamentals(bad)["score"], 0)
        self.assertEqual(assess_fundamentals(None)["score"], 0)

    def test_candidate_selection(self) -> None:
        items = [
            _item("Infosys net profit rises, beats estimates", 3, ["EARNINGS"]),
            _item("Wipro misses estimates, profit falls", -3, ["EARNINGS"]),
        ]
        picked = select_candidates(items, {"INFY": ["Infosys"], "WIPRO": ["Wipro"], "TCS": ["TCS"]})
        self.assertEqual(set(picked), {"INFY"})

    def test_peer_pe_comparison(self) -> None:
        data = {"revenueGrowth": 0.1, "earningsGrowth": 0.1, "trailingPE": 50.0}
        self.assertTrue(any("above sector" in n for n in assess_fundamentals(data, 20.0)["notes"]))
        cheap = {"revenueGrowth": 0.1, "earningsGrowth": 0.1, "trailingPE": 15.0}
        self.assertTrue(any("below sector" in n for n in assess_fundamentals(cheap, 20.0)["notes"]))

    def test_verdicts(self) -> None:
        items = [
            _item("Infosys net profit rises, beats estimates", 3, ["EARNINGS"]),
            _item("Infosys upgrade buy rating target price Rs 1,700", 2, ["ANALYST_ACTION"]),
        ]
        supportive = build_research("INFY", items, ["Infosys"], 1500.0, load_fundamentals=False)
        self.assertEqual(supportive["verdict"], "SUPPORTIVE")
        empty = build_research("INFY", [], ["Infosys"], 1500.0, load_fundamentals=False)
        self.assertEqual(empty["verdict"], "INSUFFICIENT")
        bad = [_item("Infosys misses estimates, profit falls", -3, ["EARNINGS"])]
        self.assertEqual(
            build_research("INFY", bad, ["Infosys"], 1500.0, load_fundamentals=False)["verdict"],
            "AVOID",
        )


if __name__ == "__main__":
    unittest.main()
