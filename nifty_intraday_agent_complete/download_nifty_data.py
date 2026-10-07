"""Download NIFTY 50 1-minute historical candles from Zerodha Kite.

The downloader intentionally uses the existing Kite credentials already used by
NiftyAgent. It writes the normalized CSV format expected by backtester.py.

Example:
    python download_nifty_data.py --months 6

Kite's historical API limits a single 1-minute request to at most 60 calendar
days, so this script fetches smaller chunks and throttles requests.
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()

IST = ZoneInfo(os.getenv("MARKET_TIMEZONE", "Asia/Kolkata"))
DEFAULT_TOKEN = int(os.getenv("NIFTY_INSTRUMENT_TOKEN", "256265"))
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=IST)


def market_window(day: datetime) -> tuple[datetime, datetime]:
    start = day.replace(
        hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0
    )
    end = day.replace(
        hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0
    )
    return start, end


def fetch_chunk(
    kite: KiteConnect,
    token: int,
    start: datetime,
    end: datetime,
) -> list[dict]:
    candles = kite.historical_data(
        instrument_token=token,
        from_date=start,
        to_date=end,
        interval="minute",
        continuous=False,
        oi=False,
    )
    return candles


def download(
    start_date: datetime,
    end_date: datetime,
    token: int,
    chunk_days: int,
    delay_seconds: float,
) -> pd.DataFrame:
    if start_date > end_date:
        raise ValueError("start date must be on or before end date")
    if chunk_days < 1 or chunk_days > 60:
        raise ValueError("chunk-days must be between 1 and 60")

    api_key = os.getenv("KITE_API_KEY", "")
    access_token = os.getenv("KITE_ACCESS_TOKEN", "")
    if not api_key or not access_token:
        raise RuntimeError(
            "KITE_API_KEY and KITE_ACCESS_TOKEN are required in .env. "
            "Use the same credentials already configured for NiftyAgent."
        )

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    all_rows: list[dict] = []
    cursor = start_date

    while cursor.date() <= end_date.date():
        chunk_end_date = min(
            cursor.date() + timedelta(days=chunk_days - 1), end_date.date()
        )
        chunk_start, _ = market_window(cursor)
        _, chunk_end = market_window(
            datetime.combine(chunk_end_date, datetime.min.time(), tzinfo=IST)
        )
        if chunk_end > end_date:
            chunk_end = end_date

        print(
            f"Fetching {chunk_start.date()} -> {chunk_end.date()} "
            f"({chunk_start:%Y-%m-%d %H:%M} to {chunk_end:%Y-%m-%d %H:%M})"
        )

        try:
            rows = fetch_chunk(kite, token, chunk_start, chunk_end)
        except Exception as exc:
            raise RuntimeError(
                f"Kite historical request failed for {chunk_start.date()} "
                f"to {chunk_end.date()}: {exc}"
            ) from exc

        all_rows.extend(rows)
        print(f"  received {len(rows):,} candles")
        cursor = datetime.combine(
            chunk_end_date + timedelta(days=1),
            datetime.min.time(),
            tzinfo=IST,
        )
        time.sleep(max(delay_seconds, 0.0))

    if not all_rows:
        raise RuntimeError(
            "Kite returned no candles. Check the date range, instrument token, "
            "historical-data entitlement, and access token."
        )

    df = pd.DataFrame(all_rows)
    df = df.rename(columns={"date": "timestamp"})
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"Kite response is missing columns: {missing}")

    df = df[required].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(IST)
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = (
        df.dropna(subset=["timestamp", "open", "high", "low", "close"])
        .drop_duplicates(subset=["timestamp"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # Keep only the regular NSE session. The API can return candles outside the
    # requested window in edge cases, so enforce the session boundary locally.
    local_time = df["timestamp"].dt.tz_convert(IST)
    minutes = local_time.dt.hour * 60 + local_time.dt.minute
    open_minute = MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MINUTE
    close_minute = MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MINUTE
    df = df[(minutes >= open_minute) & (minutes < close_minute)].copy()

    zero_volume = int(df["volume"].fillna(0).eq(0).all())
    if zero_volume:
        print(
            "\nWARNING: NIFTY 50 is an index, so its historical traded volume is "
            "normally zero. V2.1's relative-volume confirmation therefore cannot "
            "be validated from this spot/index CSV alone. The backtest should "
            "either use a futures-volume series or explicitly run a price-only "
            "mode.\n"
        )

    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Download NIFTY 50 1-minute candles")
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", help="End date YYYY-MM-DD")
    parser.add_argument(
        "--months",
        type=int,
        default=6,
        help="Months to look back when --start is omitted (default: 6)",
    )
    parser.add_argument(
        "--token",
        type=int,
        default=DEFAULT_TOKEN,
        help="Kite NIFTY 50 instrument token (default: 256265)",
    )
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=30,
        help="Calendar days per historical request (1-60, default: 30)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between historical API requests in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--output",
        default="data/nifty_1m.csv",
        help="Output CSV path (default: data/nifty_1m.csv)",
    )
    args = parser.parse_args()

    now = datetime.now(IST)
    end = parse_date(args.end) if args.end else now
    if args.start:
        start = parse_date(args.start)
    else:
        # Calendar-month approximation is deliberate; the API ignores non-trading
        # days and the exact trading-day count is determined by returned candles.
        start = end - timedelta(days=max(args.months, 1) * 31)

    df = download(
        start_date=start,
        end_date=end,
        token=args.token,
        chunk_days=args.chunk_days,
        delay_seconds=args.delay,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)

    print(f"\nSaved {len(df):,} candles to {output}")
    print(f"Range: {df['timestamp'].min()} -> {df['timestamp'].max()}")
    print(f"Trading days: {df['timestamp'].dt.date.nunique():,}")


if __name__ == "__main__":
    main()
