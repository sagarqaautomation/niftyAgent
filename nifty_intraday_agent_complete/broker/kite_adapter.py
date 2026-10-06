"""
Zerodha Kite Connect adapter boundary.

This module intentionally separates broker-specific connectivity from the
signal engine. Credentials come from .env.

Before enabling live data:
1. Confirm your Kite Connect application is active.
2. Complete the current login/authentication flow for your account.
3. Put the resulting access token in KITE_ACCESS_TOKEN.
4. Confirm the current NIFTY instrument token from the broker instrument dump.

No order placement is implemented here.
"""

from collections.abc import Callable, Sequence
from datetime import date, datetime
from threading import RLock
from typing import Any, cast
from zoneinfo import ZoneInfo

from config import settings

try:
    from kiteconnect import KiteTicker as _KiteTicker, KiteConnect as _KiteConnect  # type: ignore[reportMissingTypeStubs]

    KiteTicker: Any = _KiteTicker
    KiteConnect: Any = _KiteConnect
except ImportError:
    KiteTicker = None
    KiteConnect = None

Tick = dict[str, Any]
TickCallback = Callable[[Sequence[Tick]], None]
StatusCallback = Callable[[str, str | None], None]

class KiteMarketData:
    def __init__(self):
        self.kite: Any | None = None
        self.ticker: Any | None = None
        self.callback: TickCallback | None = None
        self.status_callback: StatusCallback | None = None
        self.instrument_tokens: list[int] = []
        self.http_lock = RLock()

    def connect(self) -> "KiteMarketData":
        if KiteConnect is None:
            raise RuntimeError("kiteconnect package is not installed")
        if not settings.kite_api_key:
            raise RuntimeError("KITE_API_KEY is missing in .env")
        if not settings.kite_access_token:
            raise RuntimeError(
                "KITE_ACCESS_TOKEN is missing. Complete the current Kite authentication flow first."
            )

        kite = KiteConnect(api_key=settings.kite_api_key)
        kite.set_access_token(settings.kite_access_token)
        self.kite = kite

        self.ticker = KiteTicker(
            settings.kite_api_key,
            settings.kite_access_token
        )

        return self

    def on_tick(self, callback: TickCallback) -> None:
        self.callback = callback
        ticker = self.ticker
        if ticker is None:
            raise RuntimeError("Call connect() first")

        def on_ticks(_ws: Any, ticks: Sequence[Tick]) -> None:
            if self.callback:
                self.callback(ticks)

        def on_connect(ws: Any, _response: Any) -> None:
            if self.instrument_tokens:
                ws.subscribe(self.instrument_tokens)
                ws.set_mode(ws.MODE_FULL, self.instrument_tokens)
                if settings.nifty_instrument_token:
                    ws.set_mode(ws.MODE_LTP, [int(settings.nifty_instrument_token)])
            self._report_status("CONNECTED")

        def on_close(
            _ws: Any,
            code: int | str | None,
            reason: str | None,
        ) -> None:
            print("Kite WebSocket closed:", code, reason)
            self._report_status("DISCONNECTED", f"{code}: {reason}")

        def on_error(
            _ws: Any,
            code: int | str | None,
            reason: str | None,
        ) -> None:
            print("Kite WebSocket error:", code, reason)
            self._report_status("ERROR", f"{code}: {reason}")

        ticker.on_ticks = on_ticks
        ticker.on_connect = on_connect
        ticker.on_close = on_close
        ticker.on_error = on_error

    def on_status(self, callback: StatusCallback) -> None:
        self.status_callback = callback

    def _report_status(self, state: str, detail: str | None = None) -> None:
        if self.status_callback:
            self.status_callback(state, detail)

    def subscribe(self, instrument_tokens: Sequence[int]) -> None:
        if not self.ticker:
            raise RuntimeError("Call connect() first")
        tokens = [int(x) for x in instrument_tokens]
        self.ticker.subscribe(tokens)
        self.ticker.set_mode(self.ticker.MODE_FULL, tokens)

    def historical_data(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
    ) -> list[dict[str, Any]]:
        if not self.kite:
            raise RuntimeError("Call connect() first")
        with self.http_lock:
            return self.kite.historical_data(
                int(instrument_token), from_date, to_date, interval
            )

    def nifty_spot_ltp(self) -> float:
        if not self.kite:
            raise RuntimeError("Call connect() first")
        instrument = "NSE:NIFTY 50"
        with self.http_lock:
            quotes = cast(dict[str, dict[str, Any]], self.kite.ltp([instrument]))
            quote = quotes.get(instrument)
        if not quote or quote.get("last_price") is None:
            raise RuntimeError("Kite returned no NIFTY 50 LTP")
        return float(quote["last_price"])

    def quotes(self, instruments: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not self.kite:
            raise RuntimeError("Call connect() first")
        if not instruments:
            return {}
        with self.http_lock:
            result = self.kite.quote(list(instruments))
        return cast(dict[str, dict[str, Any]], result)

    def set_instrument_tokens(self, instrument_tokens: Sequence[int]) -> None:
        self.instrument_tokens = list(dict.fromkeys(int(token) for token in instrument_tokens))

    def get_front_future(self, name: str = "NIFTY") -> dict[str, Any]:
        instruments = self.get_instruments("NFO")
        today = datetime.now(ZoneInfo(settings.market_timezone)).date()
        contracts: list[tuple[date, dict[str, Any]]] = []
        for instrument in instruments:
            expiry = instrument.get("expiry")
            if isinstance(expiry, datetime):
                expiry = expiry.date()
            if (
                instrument.get("name") == name
                and instrument.get("instrument_type") == "FUT"
                and isinstance(expiry, date)
                and expiry >= today
            ):
                contracts.append((expiry, instrument))

        if not contracts:
            raise RuntimeError(f"No unexpired {name} futures contract found in Kite instruments")

        expiry, contract = min(contracts, key=lambda item: item[0])
        return {**contract, "expiry": expiry}

    def start(self) -> None:
        if not self.ticker:
            raise RuntimeError("Call connect() first")
        self.ticker.connect(threaded=True)

    def stop(self) -> None:
        if self.ticker:
            try:
                self.ticker.close()
            except Exception:
                pass

    def get_instruments(self, exchange: str = "NFO") -> list[dict[str, Any]]:
        if not self.kite:
            raise RuntimeError("Call connect() first")
        return cast(list[dict[str, Any]], self.kite.instruments(exchange))
