import unittest
from typing import Any

from premarket_collector import (
    parse_investing_india_50,
    parse_nse_market_data,
)


class PreMarketCollectorTests(unittest.TestCase):
    def test_investing_futures_green_and_red_readings(self):
        green_html = (
            '<span data-test="instrument-price-last">25,400.00</span>'
            '<span data-test="instrument-price-change-percent">+114.20 (+0.45%)</span>'
        )
        red_html = (
            '<span data-test="instrument-price-last">25,100.00</span>'
            '<span data-test="instrument-price-change-percent">-80.50 (-0.32%)</span>'
        )

        green = parse_investing_india_50(green_html, "2026-10-14T09:00:00+05:30")
        red = parse_investing_india_50(red_html, "2026-10-14T09:00:00+05:30")

        self.assertEqual(green["direction"], "GREEN")
        self.assertEqual(green["change_pct"], 0.45)
        self.assertEqual(green["price"], 25400.0)
        self.assertEqual(red["direction"], "RED")
        self.assertEqual(red["change_pct"], -0.32)

    def test_nse_gift_and_preopen_breadth_are_parsed_with_timestamp(self):
        market_status: dict[str, Any] = {
            "giftnifty": {
                "LASTPRICE": 22803,
                "PERCHANGE": -0.21,
                "TIMESTMP": "13-Oct-2026 19:33",
            }
        }
        advance_decline: dict[str, Any] = {
            "timestamp": "13-Oct-2026 16:00:00",
            "advance": {
                "count": {
                    "Advances": 2127,
                    "Declines": 1417,
                    "Unchang": 100,
                    "Total": 3644,
                }
            },
        }

        gift, breadth, ratio = parse_nse_market_data(
            market_status,
            advance_decline,
            observed_at="2026-10-14T09:00:00+05:30",
        )

        self.assertEqual(gift["direction"], "RED")
        self.assertEqual(gift["change_pct"], -0.21)
        self.assertEqual(breadth["availability"], "latest_published_session")
        self.assertEqual(breadth["bias"], "BULLISH")
        self.assertEqual(breadth["advances"], 2127)
        self.assertEqual(breadth["declines"], 1417)
        self.assertEqual(ratio, 2127 / 1417)

    def test_after_open_old_breadth_is_not_marked_current(self):
        market_status: dict[str, Any] = {}
        advance_decline: dict[str, Any] = {
                "timestamp": "12-Oct-2026 16:00:00",
                "advance": {"count": {"Advances": 100, "Declines": 200}},
            }
        _, breadth, ratio = parse_nse_market_data(
            market_status,
            advance_decline,
            observed_at="2026-10-13T09:20:00+05:30",
        )

        self.assertEqual(breadth["availability"], "latest_published_session")
        self.assertFalse(breadth["is_current_session"])
        self.assertIsNone(ratio)

    def test_current_session_breadth_is_available_before_open(self):
        market_status: dict[str, Any] = {}
        advance_decline: dict[str, Any] = {
                "timestamp": "14-Oct-2026 09:08:00",
                "advance": {"count": {"Advances": 200, "Declines": 100}},
            }
        _, breadth, ratio = parse_nse_market_data(
            market_status,
            advance_decline,
            observed_at="2026-10-14T09:10:00+05:30",
        )

        self.assertEqual(breadth["availability"], "available")
        self.assertTrue(breadth["is_current_session"])
        self.assertEqual(ratio, 2.0)


if __name__ == "__main__":
    unittest.main()
