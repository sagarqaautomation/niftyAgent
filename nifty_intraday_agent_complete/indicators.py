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


def _prior_day_levels(index: pd.DatetimeIndex, high: pd.Series, low: pd.Series) -> tuple[pd.Series, pd.Series]:
    session_dates = pd.Series(index.date, index=index)
    daily_high = high.groupby(session_dates).max()
    daily_low = low.groupby(session_dates).min()
    prior_high = session_dates.map(daily_high.shift(1))
    prior_low = session_dates.map(daily_low.shift(1))
    prior_high.index = index
    prior_low.index = index
    return prior_high, prior_low


def _opening_range(index: pd.DatetimeIndex, high: pd.Series, low: pd.Series) -> tuple[pd.Series, pd.Series]:
    session_dates = pd.Series(index.date, index=index)
    session_start = pd.Series(index.normalize(), index=index)
    minutes_from_open = (index - index.normalize() - pd.Timedelta(hours=9, minutes=15)).total_seconds() / 60
    in_opening_range = pd.Series((minutes_from_open >= 0) & (minutes_from_open < 15), index=index)
    range_high = high.where(in_opening_range).groupby(session_dates).cummax()
    range_low = low.where(in_opening_range).groupby(session_dates).cummin()
    # Freeze the completed first 15-minute range for the rest of the session.
    completed_high = range_high.groupby(session_dates).ffill()
    completed_low = range_low.groupby(session_dates).ffill()
    completed_high = completed_high.where(minutes_from_open >= 15)
    completed_low = completed_low.where(minutes_from_open >= 15)
    completed_high.index = index
    completed_low.index = index
    return completed_high, completed_low


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_index()
    if df.empty:
        return df

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    volume = df["volume"].fillna(0).astype(float)

    df["ema9"] = close.ewm(span=9, adjust=False).mean()
    df["ema21"] = close.ewm(span=21, adjust=False).mean()
    df["ema9_slope"] = df["ema9"].diff(3)
    df["ema21_slope"] = df["ema21"].diff(3)

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi14"] = (100 - (100 / (1 + rs))).fillna(50)

    typical = (high + low + close) / 3
    volume_for_vwap = volume.replace(0, np.nan)
    if isinstance(df.index, pd.DatetimeIndex):
        sessions = pd.Series(df.index.date, index=df.index)
        df["vwap"] = (
            (typical * volume_for_vwap).groupby(sessions).cumsum()
            / volume_for_vwap.groupby(sessions).cumsum()
        )
    else:
        df["vwap"] = (typical * volume_for_vwap).cumsum() / volume_for_vwap.cumsum()

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()
    df["atr_pct"] = (df["atr14"] / close.replace(0, np.nan)) * 100

    # ADX is deliberately calculated on the same timeframe as the trend decision.
    df["adx14"] = pd.Series(
        talib.ADX(high.to_numpy(), low.to_numpy(), close.to_numpy(), timeperiod=14),
        index=df.index,
    )
    df["plus_di14"] = pd.Series(
        talib.PLUS_DI(high.to_numpy(), low.to_numpy(), close.to_numpy(), timeperiod=14),
        index=df.index,
    )
    df["minus_di14"] = pd.Series(
        talib.MINUS_DI(high.to_numpy(), low.to_numpy(), close.to_numpy(), timeperiod=14),
        index=df.index,
    )

    # Relative volume excludes the current candle to avoid contaminating the baseline.
    df["relative_volume20"] = volume / volume.shift(1).rolling(20).median().replace(0, np.nan)

    candle_range = (high - low).replace(0, np.nan)
    df["body_pct"] = (close - open_).abs() / candle_range
    df["close_location"] = (close - low) / candle_range

    if isinstance(df.index, pd.DatetimeIndex):
        prior_high, prior_low = _prior_day_levels(df.index, high, low)
        df["prior_day_high"] = prior_high
        df["prior_day_low"] = prior_low
        opening_high, opening_low = _opening_range(df.index, high, low)
        df["opening_range_high"] = opening_high
        df["opening_range_low"] = opening_low

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
