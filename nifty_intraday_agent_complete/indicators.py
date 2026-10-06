import numpy as np
import pandas as pd
from typing import Any

import talib as _talib  # type: ignore[reportMissingTypeStubs]

talib: Any = _talib

CANDLE_PATTERN_RULES: tuple[tuple[str, str, int], ...] = (
    ("Bullish Engulfing", "CDLENGULFING", 1),
    ("Hammer", "CDLHAMMER", 1),
    ("Morning Star", "CDLMORNINGSTAR", 1),
    ("Piercing Line", "CDLPIERCING", 1),
    ("Bullish Harami", "CDLHARAMI", 1),
    ("Three White Soldiers", "CDL3WHITESOLDIERS", 1),
    ("Inverted Hammer", "CDLINVERTEDHAMMER", 1),
    ("Dragonfly Doji", "CDLDRAGONFLYDOJI", 1),
    ("Bullish Abandoned Baby", "CDLABANDONEDBABY", 1),
    ("Three Inside Up", "CDL3INSIDE", 1),
    ("Three Outside Up", "CDL3OUTSIDE", 1),
    ("Bullish Kicker", "CDLKICKING", 1),
    ("Rising Three Methods", "CDLRISEFALL3METHODS", 1),
    ("Concealing Baby Swallow", "CDLCONCEALBABYSWALL", 1),
    ("Bullish Mat Hold", "CDLMATHOLD", 1),
    ("Bullish Separating Lines", "CDLSEPARATINGLINES", 1),
    ("Bullish Belt Hold", "CDLBELTHOLD", 1),
    ("Bullish Three-Line Strike", "CDL3LINESTRIKE", 1),
    ("Ladder Bottom", "CDLLADDERBOTTOM", 1),
    ("Meeting Lines", "CDLCOUNTERATTACK", 1),
    ("Bearish Engulfing", "CDLENGULFING", -1),
    ("Bearish Belt Hold", "CDLBELTHOLD", -1),
    ("Three Black Crows", "CDL3BLACKCROWS", -1),
    ("Bearish Three-Line Strike", "CDL3LINESTRIKE", -1),
    ("Hanging Man", "CDLHANGINGMAN", -1),
    ("Upside Gap Two Crows", "CDLUPSIDEGAP2CROWS", -1),
    ("Evening Star", "CDLEVENINGSTAR", -1),
    ("Shooting Star", "CDLSHOOTINGSTAR", -1),
    ("Bearish Harami", "CDLHARAMI", -1),
    ("Bearish Doji Star", "CDLDOJISTAR", -1),
    ("Bearish Abandoned Baby", "CDLABANDONEDBABY", -1),
    ("Bearish Kicker", "CDLKICKING", -1),
    ("Three Inside Down", "CDL3INSIDE", -1),
    ("Three Outside Down", "CDL3OUTSIDE", -1),
    ("Bearish Mat Hold / Falling Three Methods", "CDLRISEFALL3METHODS", -1),
    ("Dark Cloud Cover", "CDLDARKCLOUDCOVER", -1),
)

CandlestickPattern = dict[str, str]

def detect_candlestick_pattern_rows(
    df: pd.DataFrame,
) -> dict[pd.Timestamp, list[CandlestickPattern]]:
    if df.empty:
        return {}

    open_values = df["open"].to_numpy(dtype=np.float64)
    high_values = df["high"].to_numpy(dtype=np.float64)
    low_values = df["low"].to_numpy(dtype=np.float64)
    close_values = df["close"].to_numpy(dtype=np.float64)
    detected: dict[pd.Timestamp, list[CandlestickPattern]] = {}

    for name, function_name, polarity in CANDLE_PATTERN_RULES:
        values = np.asarray(getattr(talib, function_name)(
            open_values, high_values, low_values, close_values
        ))
        for position in np.flatnonzero(values * polarity > 0):
            timestamp = pd.Timestamp(df.index[int(position)])
            detected.setdefault(timestamp, []).append({
                "name": name,
                "direction": "BULLISH" if polarity > 0 else "BEARISH",
            })

    for position in range(1, len(df)):
        previous = df.iloc[position - 1]
        current = df.iloc[position]
        tolerance = max(abs(float(current["close"])) * 0.0005, 1e-8)
        if (
            previous["close"] < previous["open"]
            and current["close"] > current["open"]
            and abs(float(previous["low"]) - float(current["low"])) <= tolerance
        ):
            timestamp = pd.Timestamp(df.index[position])
            detected.setdefault(timestamp, []).append({
                "name": "Tweezer Bottom",
                "direction": "BULLISH",
            })
        elif (
            previous["close"] > previous["open"]
            and current["close"] < current["open"]
            and abs(float(previous["high"]) - float(current["high"])) <= tolerance
        ):
            timestamp = pd.Timestamp(df.index[position])
            detected.setdefault(timestamp, []).append({
                "name": "Tweezer Top",
                "direction": "BEARISH",
            })

    return detected

def detect_latest_candlestick_patterns(df: pd.DataFrame) -> list[CandlestickPattern]:
    if df.empty:
        return []
    latest_timestamp = pd.Timestamp(df.index[-1])
    recent_patterns = detect_candlestick_pattern_rows(df.tail(20))
    return recent_patterns.get(latest_timestamp, [])

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if df.empty:
        return df

    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))
    df["rsi14"] = df["rsi14"].fillna(50)

    typical = (df["high"] + df["low"] + df["close"]) / 3
    volume = df["volume"].replace(0, np.nan)
    if isinstance(df.index, pd.DatetimeIndex):
        sessions = pd.Series(df.index.date, index=df.index)
        df["vwap"] = (typical * volume).groupby(sessions).cumsum() / volume.groupby(sessions).cumsum()
    else:
        df["vwap"] = (typical * volume).cumsum() / volume.cumsum()

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()

    return df

def candle_strength(row: pd.Series[Any]) -> float:
    rng = row["high"] - row["low"]
    if rng <= 0:
        return 0.0
    return abs(row["close"] - row["open"]) / rng

def support_resistance(
    df: pd.DataFrame,
    lookback: int = 20,
) -> tuple[float | None, float | None]:
    if len(df) < lookback:
        return None, None
    recent = df.tail(lookback)
    return float(recent["low"].min()), float(recent["high"].max())
