from collections import defaultdict
from datetime import datetime, timezone
import pandas as pd
from indicators import add_indicators

class CandleEngine:
    """Converts ticks into minute candles and maintains 1m/5m DataFrames."""

    def __init__(self, max_rows=500):
        self.ticks = []
        self.max_rows = max_rows
        self.frames = {"1min": pd.DataFrame(), "5min": pd.DataFrame()}

    def add_tick(self, timestamp, price, volume=0):
        self.ticks.append({
            "timestamp": pd.Timestamp(timestamp),
            "price": float(price),
            "volume": float(volume or 0)
        })
        self.ticks = self.ticks[-100000:]

        raw = pd.DataFrame(self.ticks)
        if raw.empty:
            return None

        raw = raw.set_index("timestamp")
        minute = raw.resample("1min").agg(
            open=("price","first"),
            high=("price","max"),
            low=("price","min"),
            close=("price","last"),
            volume=("volume","sum")
        ).dropna()

        five = minute.resample("5min").agg(
            open=("open","first"),
            high=("high","max"),
            low=("low","min"),
            close=("close","last"),
            volume=("volume","sum")
        ).dropna()

        self.frames["1min"] = add_indicators(minute.tail(self.max_rows))
        self.frames["5min"] = add_indicators(five.tail(self.max_rows))

        return self.frames
