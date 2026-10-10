from __future__ import annotations

import argparse
import calendar
import json
import sqlite3
import time
from datetime import datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import pandas as pd

from config import settings

try:
    from kiteconnect import KiteConnect  # type: ignore[reportMissingTypeStubs]
except ImportError as exc:
    raise RuntimeError(
        "kiteconnect is missing. Install the project requirements before downloading."
    ) from exc


MARKET_TIMEZONE = ZoneInfo(settings.market_timezone)
SESSION_OPEN = datetime_time(9, 15)
SESSION_CLOSE = datetime_time(15, 30)


def subtract_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_zero_based = divmod(month_index, 12)
    month = month_zero_based + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def signal_symbols_for_today(now: datetime | None = None) -> list[str]:
    market_now = now or datetime.now(MARKET_TIMEZONE)
    if market_now.tzinfo is None:
        market_now = market_now.replace(tzinfo=MARKET_TIMEZONE)
    else:
        market_now = market_now.astimezone(MARKET_TIMEZONE)

    start_utc = market_now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    end_utc = start_utc + timedelta(days=1)
    with sqlite3.connect(settings.db_path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT symbol
            FROM equity_signals
            WHERE created_at >= ? AND created_at < ?
            ORDER BY symbol
            """,
            (start_utc.isoformat(), end_utc.isoformat()),
        ).fetchall()
    return [str(row[0]).upper() for row in rows]


def iter_request_chunks(
    start: datetime,
    end: datetime,
    chunk_days: int,
) -> Iterator[tuple[datetime, datetime]]:
    if start > end:
        raise ValueError("start must be on or before end")
    if not 1 <= chunk_days <= 60:
        raise ValueError("chunk-days must be between 1 and 60")

    cursor = start.date()
    while cursor <= end.date():
        chunk_end_date = min(
            cursor + timedelta(days=chunk_days - 1), end.date()
        )
        chunk_start = datetime.combine(cursor, SESSION_OPEN, tzinfo=MARKET_TIMEZONE)
        chunk_end = datetime.combine(
            chunk_end_date, SESSION_CLOSE, tzinfo=MARKET_TIMEZONE
        )
        if cursor == start.date() and start > chunk_start:
            chunk_start = start
        if chunk_end > end:
            chunk_end = end
        if chunk_start <= chunk_end:
            yield chunk_start, chunk_end
        cursor = chunk_end_date + timedelta(days=1)


def normalize_candles(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    frame = pd.DataFrame(rows).rename(columns={"date": "timestamp"})
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"Kite response is missing columns: {missing}")

    frame = frame[required].copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(
        MARKET_TIMEZONE
    )
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    local_minutes = frame["timestamp"].dt.hour * 60 + frame["timestamp"].dt.minute
    session_open = SESSION_OPEN.hour * 60 + SESSION_OPEN.minute
    session_close = SESSION_CLOSE.hour * 60 + SESSION_CLOSE.minute
    return (
        frame.dropna(subset=["timestamp", "open", "high", "low", "close"])
        .drop_duplicates(subset=["timestamp"])
        .loc[(local_minutes >= session_open) & (local_minutes < session_close)]
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def resolve_tokens(kite: Any, symbols: list[str]) -> tuple[dict[str, tuple[int, str]], list[str]]:
    remaining = set(symbols)
    tokens: dict[str, tuple[int, str]] = {}
    for exchange in ("NSE", "BSE"):
        if not remaining:
            break
        for instrument in kite.instruments(exchange):
            symbol = str(instrument.get("tradingsymbol", "")).upper()
            if (
                symbol in remaining
                and instrument.get("instrument_type") == "EQ"
                and instrument.get("instrument_token") is not None
            ):
                tokens[symbol] = (int(instrument["instrument_token"]), exchange)
                remaining.remove(symbol)
    return tokens, sorted(remaining)


def fetch_symbol(
    kite: Any,
    token: int,
    start: datetime,
    end: datetime,
    chunk_days: int,
    delay_seconds: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    chunks = list(iter_request_chunks(start, end, chunk_days))
    for chunk_index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        print(
            f"    chunk {chunk_index}/{len(chunks)}: "
            f"{chunk_start:%Y-%m-%d} to {chunk_end:%Y-%m-%d}",
            flush=True,
        )
        rows.extend(kite.historical_data(
            instrument_token=token,
            from_date=chunk_start,
            to_date=chunk_end,
            interval="minute",
            continuous=False,
            oi=False,
        ))
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return normalize_candles(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download six months of 1-minute history for today's equity signal symbols"
    )
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--chunk-days", type=int, default=30)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--output-dir", default="data/equity_history_6m")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.months < 1:
        parser.error("--months must be at least 1")
    if args.delay < 0:
        parser.error("--delay must be >= 0")

    if not settings.kite_api_key or not settings.kite_access_token:
        raise RuntimeError("KITE_API_KEY and KITE_ACCESS_TOKEN must be set in .env")

    symbols = signal_symbols_for_today()
    if not symbols:
        raise RuntimeError(
            "No equity signals were recorded today; there are no symbols to download."
        )

    kite: Any = KiteConnect(api_key=settings.kite_api_key)
    kite.set_access_token(settings.kite_access_token)
    tokens, missing = resolve_tokens(kite, symbols)

    end = datetime.now(MARKET_TIMEZONE)
    start = subtract_months(end, args.months).replace(
        hour=SESSION_OPEN.hour,
        minute=SESSION_OPEN.minute,
        second=0,
        microsecond=0,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "symbols_requested": symbols,
        "missing_instruments": missing,
        "results": {},
    }

    print(
        f"Downloading {args.months} months for {len(symbols)} symbols "
        f"({start.date()} to {end.date()})",
        flush=True,
    )
    for index, symbol in enumerate(symbols, start=1):
        destination = output_dir / f"{symbol}_1m.csv"
        if symbol not in tokens:
            report["results"][symbol] = {"status": "missing_instrument"}
            continue
        if destination.exists() and not args.overwrite:
            report["results"][symbol] = {"status": "skipped_existing_file"}
            print(f"[{index}/{len(symbols)}] {symbol}: output exists, skipped")
            continue

        token, exchange = tokens[symbol]
        print(f"[{index}/{len(symbols)}] {symbol} ({exchange})", flush=True)
        try:
            candles = fetch_symbol(
                kite,
                token,
                start,
                end,
                args.chunk_days,
                args.delay,
            )
            if candles.empty:
                report["results"][symbol] = {
                    "status": "no_data",
                    "exchange": exchange,
                    "instrument_token": token,
                }
                continue
            candles.to_csv(destination, index=False)
            report["results"][symbol] = {
                "status": "downloaded",
                "exchange": exchange,
                "instrument_token": token,
                "candles": len(candles),
                "first_timestamp": candles["timestamp"].min().isoformat(),
                "last_timestamp": candles["timestamp"].max().isoformat(),
                "zero_volume_rows": int(candles["volume"].fillna(0).eq(0).sum()),
                "file": str(destination),
            }
            print(f"    saved {len(candles):,} candles to {destination}", flush=True)
        except Exception as exc:
            report["results"][symbol] = {
                "status": "failed",
                "exchange": exchange,
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"    failed: {type(exc).__name__}: {exc}", flush=True)

    manifest = output_dir / "manifest.json"
    manifest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    downloaded = sum(
        result.get("status") == "downloaded"
        for result in report["results"].values()
    )
    failed = sum(
        result.get("status") in {"failed", "missing_instrument", "no_data"}
        for result in report["results"].values()
    )
    print(
        f"Completed: {downloaded}/{len(symbols)} downloaded; "
        f"{failed} missing/failed/no-data. Manifest: {manifest}",
        flush=True,
    )


if __name__ == "__main__":
    main()