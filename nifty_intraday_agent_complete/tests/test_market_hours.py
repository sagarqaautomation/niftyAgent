import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from market_hours import is_current_session_candle, is_market_open


class MarketHoursTests(unittest.TestCase):
    def test_weekday_session_boundaries(self) -> None:
        timezone = ZoneInfo("Asia/Kolkata")
        self.assertFalse(is_market_open(datetime(2026, 10, 8, 8, 59, tzinfo=timezone)))
        self.assertTrue(is_market_open(datetime(2026, 10, 8, 9, 0, tzinfo=timezone)))
        self.assertTrue(is_market_open(datetime(2026, 10, 8, 15, 29, tzinfo=timezone)))
        self.assertFalse(is_market_open(datetime(2026, 10, 8, 15, 30, tzinfo=timezone)))

    def test_weekends_are_closed(self) -> None:
        timezone = ZoneInfo("Asia/Kolkata")
        self.assertFalse(is_market_open(datetime(2026, 10, 10, 10, 0, tzinfo=timezone)))

    def test_aware_times_are_converted_to_market_timezone(self) -> None:
        utc_time = datetime.fromisoformat("2026-10-08T03:30:00+00:00")
        self.assertTrue(is_market_open(utc_time))

    def test_previous_session_close_is_not_a_current_session_candle(self) -> None:
        timezone = ZoneInfo("Asia/Kolkata")
        previous_close = datetime(2026, 10, 7, 15, 39, tzinfo=timezone)
        current_tick = datetime(2026, 10, 8, 9, 9, tzinfo=timezone)
        self.assertFalse(is_current_session_candle(previous_close, current_tick))

    def test_current_session_candle_is_accepted(self) -> None:
        timezone = ZoneInfo("Asia/Kolkata")
        candle_time = datetime(2026, 10, 8, 9, 9, tzinfo=timezone)
        tick_time = datetime(2026, 10, 8, 9, 10, tzinfo=timezone)
        self.assertTrue(is_current_session_candle(candle_time, tick_time))


if __name__ == "__main__":
    unittest.main()