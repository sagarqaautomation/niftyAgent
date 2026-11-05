import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

import database


class EquityOutcomeDatabaseTests(unittest.TestCase):
    def test_market_day_filter_uses_market_timezone_without_deleting_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "equity.db"
            with sqlite3.connect(database_path) as connection:
                connection.execute("""
                    CREATE TABLE equity_signals (
                        id INTEGER PRIMARY KEY,
                        status TEXT NOT NULL,
                        resolved_at TEXT
                    )
                """)
                connection.executemany(
                    "INSERT INTO equity_signals VALUES (?, ?, ?)",
                    [
                        (1, "TARGET_HIT", "2026-10-20T18:29:59+00:00"),
                        (2, "TARGET_HIT", "2026-10-20T18:30:00+00:00"),
                        (3, "TARGET_HIT", "2026-10-21T18:30:00+00:00"),
                    ],
                )

            test_settings = replace(
                database.settings,
                db_path=str(database_path),
                market_timezone="Asia/Kolkata",
            )
            with patch.object(database, "settings", test_settings):
                today_rows = database.list_equity_signals_by_status(
                    "TARGET_HIT", market_day=date(2026, 10, 21)
                )
                all_rows = database.list_equity_signals_by_status("TARGET_HIT")

            self.assertEqual([row["id"] for row in today_rows], [2])
            self.assertEqual(len(all_rows), 3)


if __name__ == "__main__":
    unittest.main()