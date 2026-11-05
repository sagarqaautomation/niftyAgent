import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from download_equity_history import iter_request_chunks, normalize_candles, subtract_months


class DownloadEquityHistoryTests(unittest.TestCase):
    def test_subtract_months_clamps_to_last_day(self) -> None:
        value = datetime(2026, 8, 31, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertEqual(subtract_months(value, 6), datetime(
            2026, 2, 28, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata")
        ))

    def test_request_chunks_respect_limit_and_cover_range(self) -> None:
        timezone = ZoneInfo("Asia/Kolkata")
        start = datetime(2026, 4, 8, 9, 15, tzinfo=timezone)
        end = datetime(2026, 10, 8, 15, 30, tzinfo=timezone)

        chunks = list(iter_request_chunks(start, end, 30))

        self.assertEqual(chunks[0][0], start)
        self.assertEqual(chunks[-1][1], end)
        self.assertTrue(all((chunk_end.date() - chunk_start.date()).days < 30
                            for chunk_start, chunk_end in chunks))

    def test_request_chunks_reject_values_over_api_limit(self) -> None:
        with self.assertRaises(ValueError):
            list(iter_request_chunks(datetime(2026, 1, 1), datetime(2026, 2, 1), 61))

    def test_normalize_candles_deduplicates_and_keeps_regular_session(self) -> None:
        rows: list[dict[str, str | int]] = [
            {"date": "2026-10-08T09:14:00+05:30", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1},
            {"date": "2026-10-08T09:15:00+05:30", "open": 2, "high": 3, "low": 2, "close": 3, "volume": 10},
            {"date": "2026-10-08T09:15:00+05:30", "open": 9, "high": 10, "low": 9, "close": 10, "volume": 90},
            {"date": "2026-10-08T15:30:00+05:30", "open": 4, "high": 5, "low": 4, "close": 5, "volume": 1},
        ]

        candles = normalize_candles(rows)

        self.assertEqual(len(candles), 1)
        self.assertEqual(float(candles.iloc[0]["open"]), 2.0)
        self.assertEqual(candles.iloc[0]["timestamp"].hour, 9)


if __name__ == "__main__":
    unittest.main()