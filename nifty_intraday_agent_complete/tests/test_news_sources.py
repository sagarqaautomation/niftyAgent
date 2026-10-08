import unittest
from datetime import datetime, timezone

from news_sources import (
    NewsItem,
    SOURCES,
    aggregate_news,
    classify,
    classify_categories,
    equity_news_bias,
    tag_news_symbols,
)


class NewsSourcesTests(unittest.TestCase):
    def test_multiple_verified_market_feeds_are_configured(self) -> None:
        self.assertIn("Economic Times", SOURCES)
        self.assertIn("Business Standard", SOURCES)
        self.assertIn("LiveMint", SOURCES)
        self.assertIn("Moneycontrol", SOURCES)

    def test_headlines_receive_multiple_event_categories(self) -> None:
        categories = classify_categories(
            "TCS Q2 earnings results beat estimates; analyst upgrades target price"
        )
        self.assertIn("EARNINGS", categories)
        self.assertIn("ANALYST_ACTION", categories)

    def test_earnings_and_analyst_phrases_have_directional_sentiment(self) -> None:
        self.assertEqual(
            classify("TCS beats estimates; analyst raises target price")[1],
            "BULLISH",
        )
        self.assertEqual(
            classify("Company misses estimates; analyst cuts target price")[1],
            "BEARISH",
        )

    def test_global_news_bias_ignores_company_only_headlines(self) -> None:
        now_epoch = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc).timestamp()
        items: list[NewsItem] = [
            {
                "title": "Company earnings were strong",
                "published_epoch": now_epoch - 10,
                "sentiment": 5,
                "market_bias": "BULLISH",
                "categories": ["EARNINGS"],
            },
            {
                "title": "RBI rate decision pressures markets",
                "published_epoch": now_epoch - 10,
                "sentiment": -3,
                "market_bias": "BEARISH",
                "categories": ["MARKET_MACRO"],
            },
        ]

        self.assertEqual(
            aggregate_news(items, now=datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)),
            "BEARISH",
        )

    def test_news_matches_ticker_and_company_aliases(self) -> None:
        items: list[NewsItem] = [{
            "title": "Tata Consultancy Services Q2 results beat forecasts",
            "summary": "",
            "symbols": [],
        }]

        tag_news_symbols(items, {
            "TCS": ["Tata Consultancy Services Limited"],
            "AXISBANK": ["Axis Bank Limited"],
        })

        self.assertEqual(items[0]["symbols"], ["TCS"])

    def test_per_equity_bias_uses_fresh_relevant_news_only(self) -> None:
        now = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
        now_epoch = now.timestamp()
        items: list[NewsItem] = [
            {
                "title": "TCS upgrades revenue outlook",
                "summary": "",
                "symbols": ["TCS"],
                "categories": ["EARNINGS"],
                "published_epoch": now_epoch - 60,
                "sentiment": 2,
            },
            {
                "title": "Unrelated company reports losses",
                "summary": "",
                "symbols": ["AXISBANK"],
                "categories": ["EARNINGS"],
                "published_epoch": now_epoch - 60,
                "sentiment": -5,
            },
            {
                "title": "TCS old negative article",
                "summary": "",
                "symbols": ["TCS"],
                "categories": ["EARNINGS"],
                "published_epoch": now_epoch - 7200,
                "sentiment": -5,
            },
            {
                "title": "Future TCS article",
                "summary": "",
                "symbols": ["TCS"],
                "categories": ["EARNINGS"],
                "published_epoch": now_epoch + 60,
                "sentiment": -5,
            },
        ]

        self.assertEqual(
            equity_news_bias(items, "TCS", max_age_minutes=60, now=now),
            "BULLISH",
        )

    def test_live_bias_uses_published_time_not_fetch_time(self) -> None:
        now = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
        old_item: NewsItem = {
            "title": "TCS old earnings article",
            "summary": "",
            "symbols": ["TCS"],
            "categories": ["EARNINGS"],
            "published_epoch": now.timestamp() - 7200,
            "available_epoch": now.timestamp() - 10,
            "sentiment": 4,
        }

        self.assertEqual(
            equity_news_bias([old_item], "TCS", now=now),
            "NEUTRAL",
        )
        self.assertEqual(
            equity_news_bias(
                [old_item], "TCS", now=now, availability_based=True
            ),
            "BULLISH",
        )

    def test_macro_news_applies_to_all_stocks(self) -> None:
        now = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
        item: NewsItem = {
            "title": "RBI rate decision weighs on Indian markets",
            "summary": "",
            "symbols": [],
            "categories": ["MARKET_MACRO"],
            "published_epoch": now.timestamp() - 30,
            "sentiment": -4,
        }

        self.assertEqual(
            equity_news_bias([item], "TCS", now=now),
            "BEARISH",
        )


if __name__ == "__main__":
    unittest.main()