import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database


class NewsDatabaseTests(unittest.TestCase):
    def test_old_news_table_migrates_and_stores_tags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "news.db"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("""
                    CREATE TABLE news (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source TEXT NOT NULL,
                        title TEXT NOT NULL,
                        url TEXT,
                        published_at TEXT,
                        fetched_at TEXT NOT NULL,
                        sentiment REAL,
                        market_bias TEXT,
                        UNIQUE(source, title)
                    )
                """)
                connection.commit()
            finally:
                connection.close()

            opened_connections: list[sqlite3.Connection] = []

            def connect_test_database() -> sqlite3.Connection:
                test_connection = sqlite3.connect(database_path)
                opened_connections.append(test_connection)
                return test_connection

            with patch.object(
                database,
                "connect",
                side_effect=connect_test_database,
            ):
                database.ensure_news_columns()
                database.insert_news({
                    "source": "test",
                    "title": "TCS Q2 results beat estimates",
                    "url": "https://example.test/article",
                    "published_at": "2026-10-08T10:00:00+05:30",
                    "summary": "Company results headline",
                    "sentiment": 2,
                    "market_bias": "BULLISH",
                    "categories": ["EARNINGS"],
                    "symbols": ["TCS"],
                    "published_epoch": 1_797_000_000,
                })
                database.insert_news({
                    "source": "test-older",
                    "title": "Older market headline",
                    "published_at": "2026-10-07T10:00:00+05:30",
                    "summary": "Older headline",
                    "sentiment": -1,
                    "market_bias": "NEUTRAL",
                    "categories": ["MARKET_MACRO"],
                    "symbols": [],
                    "published_epoch": 1_796_000_000,
                })
                rows = database.list_news()
                second_page = database.list_news(limit=1, offset=1)
            for test_connection in opened_connections:
                test_connection.close()

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["title"], "TCS Q2 results beat estimates")
            self.assertEqual(rows[0]["event_categories"], ["EARNINGS"])
            self.assertEqual(rows[0]["symbols"], ["TCS"])
            self.assertEqual(rows[0]["summary"], "Company results headline")
            self.assertEqual(second_page[0]["title"], "Older market headline")


if __name__ == "__main__":
    unittest.main()