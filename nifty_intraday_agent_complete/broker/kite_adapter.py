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

from config import settings

try:
    from kiteconnect import KiteTicker, KiteConnect
except ImportError:
    KiteTicker = None
    KiteConnect = None

class KiteMarketData:
    def __init__(self):
        self.kite = None
        self.ticker = None
        self.callback = None

    def connect(self):
        if KiteConnect is None:
            raise RuntimeError("kiteconnect package is not installed")
        if not settings.kite_api_key:
            raise RuntimeError("KITE_API_KEY is missing in .env")
        if not settings.kite_access_token:
            raise RuntimeError(
                "KITE_ACCESS_TOKEN is missing. Complete the current Kite authentication flow first."
            )

        self.kite = KiteConnect(api_key=settings.kite_api_key)
        self.kite.set_access_token(settings.kite_access_token)

        self.ticker = KiteTicker(
            settings.kite_api_key,
            settings.kite_access_token
        )

        return self

    def on_tick(self, callback):
        self.callback = callback

        def on_ticks(ws, ticks):
            if self.callback:
                self.callback(ticks)

        def on_connect(ws, response):
            token = int(settings.nifty_instrument_token)
            ws.subscribe([token])
            ws.set_mode(ws.MODE_FULL, [token])

        def on_close(ws, code, reason):
            print("Kite WebSocket closed:", code, reason)

        def on_error(ws, code, reason):
            print("Kite WebSocket error:", code, reason)

        self.ticker.on_ticks = on_ticks
        self.ticker.on_connect = on_connect
        self.ticker.on_close = on_close
        self.ticker.on_error = on_error

    def subscribe(self, instrument_tokens):
        if not self.ticker:
            raise RuntimeError("Call connect() first")
        tokens = [int(x) for x in instrument_tokens]
        self.ticker.subscribe(tokens)
        self.ticker.set_mode(self.ticker.MODE_FULL, tokens)

    def start(self):
        if not self.ticker:
            raise RuntimeError("Call connect() first")
        self.ticker.connect(threaded=True)

    def stop(self):
        if self.ticker:
            try:
                self.ticker.close()
            except Exception:
                pass

    def get_instruments(self, exchange="NFO"):
        if not self.kite:
            raise RuntimeError("Call connect() first")
        return self.kite.instruments(exchange)
