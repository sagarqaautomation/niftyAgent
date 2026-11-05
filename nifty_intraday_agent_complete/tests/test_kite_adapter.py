import unittest
from unittest.mock import Mock, patch

import requests

from broker.kite_adapter import KiteMarketData


class KiteMarketDataTests(unittest.TestCase):
    def test_get_instruments_retries_transient_connection_error(self) -> None:
        adapter = KiteMarketData()
        instruments = [{"name": "NIFTY"}]
        adapter.kite = Mock()
        adapter.kite.instruments.side_effect = [
            requests.ConnectionError("connection aborted"),
            instruments,
        ]

        with patch("broker.kite_adapter.time.sleep") as sleep:
            result = adapter.get_instruments("NFO")

        self.assertEqual(result, instruments)
        self.assertEqual(adapter.kite.instruments.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_get_instruments_does_not_retry_non_network_errors(self) -> None:
        adapter = KiteMarketData()
        adapter.kite = Mock()
        adapter.kite.instruments.side_effect = RuntimeError("invalid request")

        with patch("broker.kite_adapter.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "invalid request"):
                adapter.get_instruments("NFO")

        self.assertEqual(adapter.kite.instruments.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()