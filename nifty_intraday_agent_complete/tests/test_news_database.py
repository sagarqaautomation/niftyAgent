import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import database


class NewsDatabaseTests(unittest.TestCase):
    def test_init_db_enables_wal_and_busy_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "market.db"
            test_settings = replace(database.settings, db_path=str(database_path))
            with patch.object(database, "settings", test_settings):
                database.init_db()
                connection = database.connect()
                try:
                    journal_mode = connection.execute(
                        "PRAGMA journal_mode"
                    ).fetchone()[0]
                    busy_timeout = connection.execute(
                        "PRAGMA busy_timeout"
                    ).fetchone()[0]
                finally:
                    connection.close()

            self.assertEqual(journal_mode.lower(), "wal")
            self.assertEqual(busy_timeout, 30_000)

    def test_cost_adjusted_equity_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "equity.db"
            test_settings = replace(database.settings, db_path=str(database_path))
            with patch.object(database, "settings", test_settings):
                database.init_db()
                with database.connect() as connection:
                    connection.executemany("""
                        INSERT INTO equity_signals (
                            created_at, symbol, signal, technical_score, context_score,
                            total_score, entry_price, target_price, stop_loss,
                            signal_candle_time, reason, status
                        ) VALUES (?, ?, 'BUY', 7, 0, 7, ?, ?, ?, ?, ?, ?)
                    """, [
                        ("2026-10-23T09:30:00+05:30", "WIN", 100.0, 110.0, 95.0,
                         "2026-10-23T09:30:00+05:30", "test", "TARGET_HIT"),
                        ("2026-10-23T09:35:00+05:30", "LOSS", 100.0, 110.0, 95.0,
                         "2026-10-23T09:35:00+05:30", "test", "STOP_HIT"),
                    ])
                metrics = database.equity_signal_performance(25.0)

        self.assertEqual(metrics["resolved"], 2)
        self.assertEqual(metrics["net_win_rate_percent"], 50.0)
        self.assertEqual(metrics["gross_average_return_percent"], 2.5)
        self.assertEqual(metrics["net_average_return_percent"], 2.25)
        self.assertEqual(metrics["net_expectancy_r"], 0.45)

    def test_cost_assumption_must_be_non_negative_and_bounded(self) -> None:
        with self.assertRaises(ValueError):
            database.equity_signal_performance(-1)
        with self.assertRaises(ValueError):
            database.equity_signal_performance(1001)

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
                    "author": "Market Desk",
                    "analyst_recommendation": "BUY",
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
            self.assertEqual(rows[0]["author"], "Market Desk")
            self.assertEqual(rows[0]["analyst_recommendation"], "BUY")
            self.assertEqual(second_page[0]["title"], "Older market headline")


if __name__ == "__main__":
    unittest.main()