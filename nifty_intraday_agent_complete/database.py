import os
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from config import settings

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def connect():
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    return sqlite3.connect(settings.db_path)

def normalize_candle_timestamps() -> None:
    timezone_info = ZoneInfo(settings.market_timezone)
    with connect() as conn:
        for table in ("candles", "spot_candles"):
            rows: list[tuple[int, str, str]] = conn.execute(
                f"SELECT id, timeframe, timestamp FROM {table} ORDER BY id DESC"
            ).fetchall()
            seen: set[tuple[str, str]] = set()
            duplicates: list[tuple[int, ...]] = []
            updates: list[tuple[str, int]] = []
            for row_id, timeframe, value in rows:
                try:
                    timestamp = datetime.fromisoformat(value)
                except (TypeError, ValueError):
                    continue
                if timestamp.tzinfo is not None:
                    timestamp = timestamp.astimezone(timezone_info).replace(tzinfo=None)
                canonical = timestamp.isoformat(sep=" ")
                key = (timeframe, canonical)
                if key in seen:
                    duplicates.append((row_id,))
                else:
                    seen.add(key)
                    if value != canonical:
                        updates.append((canonical, row_id))

            if duplicates:
                conn.executemany(f"DELETE FROM {table} WHERE id=?", duplicates)
            if updates:
                conn.executemany(f"UPDATE {table} SET timestamp=? WHERE id=?", updates)

def ensure_market_status_columns() -> None:
    additions = {
        "signal_instrument": "TEXT",
        "last_exchange_tick_at": "TEXT",
        "analysis_state": "TEXT NOT NULL DEFAULT 'WARMING_UP'",
        "analysis_score": "INTEGER",
        "analysis_reason": "TEXT",
        "analysis_updated_at": "TEXT",
        "spot_last_price": "REAL",
        "spot_last_tick_at": "TEXT",
    }
    with connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(market_status)")}
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE market_status ADD COLUMN {name} {declaration}")

def ensure_signal_columns() -> None:
    additions = {
        "signal_instrument": "TEXT",
        "signal_candle_time": "TEXT",
        "spot_reference_price": "REAL",
        "spot_reference_time": "TEXT",
        "spot_trigger_price": "REAL",
        "spot_trigger_offset": "REAL",
        "spot_cross_price": "REAL",
        "spot_cross_time": "TEXT",
    }
    with connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(signals)")}
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE signals ADD COLUMN {name} {declaration}")

def ensure_equity_signal_expired_status() -> None:
    with connect() as conn:
        row = conn.execute("""
        SELECT sql FROM sqlite_master
        WHERE type='table' AND name='equity_signals'
        """).fetchone()
        if not row or not row[0] or "'EXPIRED'" in row[0]:
            return

        conn.execute("ALTER TABLE equity_signals RENAME TO equity_signals_legacy")
        conn.execute("""
        CREATE TABLE equity_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL CHECK(signal='BUY'),
            technical_score INTEGER NOT NULL,
            context_score INTEGER NOT NULL,
            total_score INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            target_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            expected_holding_minutes INTEGER,
            signal_candle_time TEXT NOT NULL,
            reason TEXT NOT NULL,
            feature_snapshot_json TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN'
                CHECK(status IN ('OPEN','TARGET_HIT','STOP_HIT','AMBIGUOUS','EXPIRED')),
            result_price REAL,
            resolved_at TEXT,
            UNIQUE(symbol, signal_candle_time)
        )
        """)
        conn.execute("""
        INSERT INTO equity_signals (
            id, created_at, symbol, signal, technical_score, context_score,
            total_score, entry_price, target_price, stop_loss,
            expected_holding_minutes, signal_candle_time, reason,
            feature_snapshot_json, status,
            result_price, resolved_at
        )
        SELECT
            id, created_at, symbol, signal, technical_score, context_score,
            total_score, entry_price, target_price, stop_loss,
            expected_holding_minutes, signal_candle_time, reason,
            feature_snapshot_json,
            CASE
                WHEN status IN ('TARGET_HIT','STOP_HIT')
                    AND resolved_at IS NOT NULL
                    AND substr(created_at, 1, 10) <> substr(resolved_at, 1, 10)
                THEN 'EXPIRED'
                ELSE status
            END,
            CASE
                WHEN status IN ('TARGET_HIT','STOP_HIT')
                    AND resolved_at IS NOT NULL
                    AND substr(created_at, 1, 10) <> substr(resolved_at, 1, 10)
                THEN NULL
                ELSE result_price
            END,
            resolved_at
        FROM equity_signals_legacy
        """)
        conn.execute("DROP TABLE equity_signals_legacy")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_equity_signals_created ON equity_signals(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_equity_signals_symbol_status ON equity_signals(symbol, status)")

def ensure_equity_feature_snapshot_column() -> None:
    with connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(equity_signals)")}
        if "feature_snapshot_json" not in columns:
            conn.execute("ALTER TABLE equity_signals ADD COLUMN feature_snapshot_json TEXT")

def expire_overdue_equity_outcomes() -> int:
    with connect() as conn:
        result = conn.execute("""
        UPDATE equity_signals
        SET status='EXPIRED', result_price=NULL
        WHERE status IN ('TARGET_HIT','STOP_HIT')
            AND resolved_at IS NOT NULL
            AND (
                substr(created_at, 1, 10) <> substr(resolved_at, 1, 10)
                OR (julianday(resolved_at) - julianday(signal_candle_time)) * 1440 > ?
            )
        """, (settings.signal_expiry_minutes,))
        return max(0, result.rowcount)

def expire_stale_equity_signals(
    now: datetime | None = None,
    expire_all_open: bool = False,
) -> int:
    timezone_info = ZoneInfo(settings.market_timezone)
    market_now = now or datetime.now(timezone_info)
    if market_now.tzinfo is None:
        market_now = market_now.replace(tzinfo=timezone_info)
    else:
        market_now = market_now.astimezone(timezone_info)

    with connect() as conn:
        rows = conn.execute("""
        SELECT id, signal_candle_time FROM equity_signals WHERE status='OPEN'
        """).fetchall()
        expired_ids: list[tuple[int]] = []
        for row_id, signal_candle_time in rows:
            try:
                signal_time = datetime.fromisoformat(signal_candle_time)
            except (TypeError, ValueError):
                expired_ids.append((row_id,))
                continue
            if signal_time.tzinfo is None:
                signal_time = signal_time.replace(tzinfo=timezone_info)
            else:
                signal_time = signal_time.astimezone(timezone_info)
            if expire_all_open or (
                signal_time.date() != market_now.date()
                or market_now - signal_time > timedelta(minutes=settings.signal_expiry_minutes)
            ):
                expired_ids.append((row_id,))

        if expired_ids:
            conn.executemany("""
            UPDATE equity_signals SET status='EXPIRED', result_price=NULL, resolved_at=?
            WHERE id=? AND status='OPEN'
            """, [(utc_now(), row_id) for (row_id,) in expired_ids])
        return len(expired_ids)

def init_db():
    with connect() as conn:
        with open("schema.sql", "r", encoding="utf-8") as f:
            conn.executescript(f.read())
    ensure_market_status_columns()
    ensure_signal_columns()
    ensure_equity_feature_snapshot_column()
    ensure_equity_signal_expired_status()
    expire_overdue_equity_outcomes()
    expire_stale_equity_signals()
    normalize_candle_timestamps()

def insert_candle(c: dict[str, Any]) -> None:
    insert_candles([c])

def insert_candles(candles: list[dict[str, Any]]) -> None:
    if not candles:
        return
    with connect() as conn:
        conn.executemany("""
        INSERT INTO candles
        (timeframe,timestamp,open,high,low,close,volume,ema9,ema21,rsi14,vwap,atr14)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(timeframe,timestamp) DO UPDATE SET
        open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
        volume=excluded.volume,ema9=excluded.ema9,ema21=excluded.ema21,
        rsi14=excluded.rsi14,vwap=excluded.vwap,atr14=excluded.atr14
        """, [(
            c["timeframe"], c["timestamp"], c["open"], c["high"], c["low"],
            c["close"], c.get("volume", 0), c.get("ema9"), c.get("ema21"),
            c.get("rsi14"), c.get("vwap"), c.get("atr14")
        ) for c in candles])

def insert_spot_candles(candles: list[dict[str, Any]]) -> None:
    if not candles:
        return
    with connect() as conn:
        conn.executemany("""
        INSERT INTO spot_candles (timeframe,timestamp,open,high,low,close)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(timeframe,timestamp) DO UPDATE SET
        open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close
        """, [(
            candle["timeframe"], candle["timestamp"], candle["open"],
            candle["high"], candle["low"], candle["close"]
        ) for candle in candles])

def insert_equity_candles(candles: list[dict[str, Any]]) -> None:
    if not candles:
        return
    with connect() as conn:
        conn.executemany("""
        INSERT INTO equity_candles
        (symbol,timeframe,timestamp,open,high,low,close,volume,ema9,ema21,rsi14,vwap,atr14)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol,timeframe,timestamp) DO UPDATE SET
        open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
        volume=excluded.volume,ema9=excluded.ema9,ema21=excluded.ema21,
        rsi14=excluded.rsi14,vwap=excluded.vwap,atr14=excluded.atr14
        """, [(
            candle["symbol"], candle["timeframe"], candle["timestamp"],
            candle["open"], candle["high"], candle["low"], candle["close"],
            candle.get("volume", 0), candle.get("ema9"), candle.get("ema21"),
            candle.get("rsi14"), candle.get("vwap"), candle.get("atr14"),
        ) for candle in candles])

def list_spot_candles(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
        SELECT id,timeframe,timestamp,open,high,low,close
        FROM spot_candles ORDER BY timestamp DESC,id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]

def update_market_status(
    state: str,
    connected_at: str | None = None,
    last_tick_at: str | None = None,
    last_error: str | None = None,
    one_minute_bars: int | None = None,
    five_minute_bars: int | None = None,
    signal_instrument: str | None = None,
    last_exchange_tick_at: str | None = None,
    analysis_state: str | None = None,
    analysis_score: int | None = None,
    analysis_reason: str | None = None,
    analysis_updated_at: str | None = None,
) -> None:
    with connect() as conn:
        existing = conn.execute("""
        SELECT connected_at, last_tick_at, last_error, one_minute_bars, five_minute_bars,
               signal_instrument, last_exchange_tick_at, analysis_state, analysis_score,
               analysis_reason, analysis_updated_at
        FROM market_status WHERE id=1
        """).fetchone()
        if existing:
            connected_at = connected_at or existing[0]
            last_tick_at = last_tick_at or existing[1]
            last_error = existing[2] if last_error is None else last_error
            one_minute_bars = one_minute_bars if one_minute_bars is not None else existing[3]
            five_minute_bars = five_minute_bars if five_minute_bars is not None else existing[4]
            signal_instrument = signal_instrument or existing[5]
            last_exchange_tick_at = last_exchange_tick_at or existing[6]
            analysis_state = analysis_state or existing[7]
            analysis_score = analysis_score if analysis_score is not None else existing[8]
            analysis_reason = analysis_reason if analysis_reason is not None else existing[9]
            analysis_updated_at = analysis_updated_at or existing[10]
        else:
            one_minute_bars = one_minute_bars or 0
            five_minute_bars = five_minute_bars or 0
            analysis_state = analysis_state or "WARMING_UP"

        conn.execute("""
        INSERT INTO market_status
        (id,state,connected_at,last_tick_at,last_error,one_minute_bars,five_minute_bars,
         signal_instrument,last_exchange_tick_at,analysis_state,analysis_score,
         analysis_reason,analysis_updated_at,updated_at)
        VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
        state=excluded.state,
        connected_at=excluded.connected_at,
        last_tick_at=excluded.last_tick_at,
        last_error=excluded.last_error,
        one_minute_bars=excluded.one_minute_bars,
        five_minute_bars=excluded.five_minute_bars,
        signal_instrument=excluded.signal_instrument,
        last_exchange_tick_at=excluded.last_exchange_tick_at,
        analysis_state=excluded.analysis_state,
        analysis_score=excluded.analysis_score,
        analysis_reason=excluded.analysis_reason,
        analysis_updated_at=excluded.analysis_updated_at,
        updated_at=excluded.updated_at
        """, (
            state, connected_at, last_tick_at, last_error,
            one_minute_bars, five_minute_bars, signal_instrument,
            last_exchange_tick_at, analysis_state, analysis_score,
            analysis_reason, analysis_updated_at, utc_now()
        ))

def get_market_status() -> dict[str, Any] | None:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM market_status WHERE id=1").fetchone()
        return dict(row) if row else None

def update_analysis_status(state: str, score: int | None, reason: str) -> None:
    with connect() as conn:
        conn.execute("""
        UPDATE market_status SET analysis_state=?, analysis_score=?,
        analysis_reason=?, analysis_updated_at=? WHERE id=1
        """, (state, score, reason, utc_now()))

def update_spot_status(price: float, timestamp: str) -> None:
    with connect() as conn:
        conn.execute("""
        UPDATE market_status SET spot_last_price=?, spot_last_tick_at=?, updated_at=?
        WHERE id=1
        """, (price, timestamp, utc_now()))

def candle_counts() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT timeframe, COUNT(*) FROM candles GROUP BY timeframe"
        ).fetchall()
        return {timeframe: count for timeframe, count in rows}

def insert_news(item: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute("""
        INSERT OR IGNORE INTO news
        (source,title,url,published_at,fetched_at,sentiment,market_bias)
        VALUES (?,?,?,?,?,?,?)
        """, (
            item["source"], item["title"], item.get("url"),
            item.get("published_at"), utc_now(), item.get("sentiment"),
            item.get("market_bias")
        ))

def insert_signal(s: dict[str, Any]) -> int:
    with connect() as conn:
        cur = conn.execute("""
        INSERT INTO signals
        (created_at,signal,technical_score,context_score,total_score,entry_price,
         target_price,stop_loss,option_symbol,option_entry,reason,news_bias,option_bias,
         signal_instrument,signal_candle_time,spot_reference_price,spot_reference_time,
         spot_trigger_price,spot_trigger_offset,spot_cross_price,spot_cross_time)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            utc_now(), s["signal"], s["technical_score"], s["context_score"],
            s["total_score"], s.get("entry_price"), s.get("target_price"),
            s.get("stop_loss"), s.get("option_symbol"), s.get("option_entry"),
            s.get("reason"), s.get("news_bias"), s.get("option_bias"),
            s.get("signal_instrument"), s.get("signal_candle_time"),
            s.get("spot_reference_price"), s.get("spot_reference_time"),
            s.get("spot_trigger_price"), s.get("spot_trigger_offset"),
            s.get("spot_cross_price"), s.get("spot_cross_time")
        ))
        row_id = cur.lastrowid
        return int(row_id) if row_id is not None else 0

def resolve_with_bar(bar: dict[str, Any]) -> None:
    with connect() as conn:
        rows = conn.execute("""
        SELECT id, signal, entry_price, target_price, stop_loss, created_at
        FROM signals WHERE status='OPEN' AND signal_instrument='NIFTY spot index'
        """).fetchall()

        for row in rows:
            sid, signal, entry, target, stop, _ = row
            if not entry or not target or not stop:
                continue

            target_hit = False
            stop_hit = False

            if signal == "CALL":
                target_hit = bar["high"] >= target
                stop_hit = bar["low"] <= stop
            elif signal == "PUT":
                target_hit = bar["low"] <= target
                stop_hit = bar["high"] >= stop

            status = None
            accuracy = None

            if target_hit and stop_hit:
                status = "AMBIGUOUS"
            elif target_hit:
                status, accuracy = "SUCCESS", 100
            elif stop_hit:
                status, accuracy = "FAILED", 0

            if status:
                conn.execute("""
                UPDATE signals SET status=?, result_price=?, resolved_at=?,
                accuracy=?, evaluation_minutes=?
                WHERE id=?
                """, (
                    status, bar["close"], utc_now(), accuracy,
                    None, sid
                ))

def expire_signals():
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.signal_expiry_minutes)
    with connect() as conn:
        rows = conn.execute("""
        SELECT id, created_at FROM signals WHERE status='OPEN'
        """).fetchall()
        for sid, created_at in rows:
            try:
                dt = datetime.fromisoformat(created_at)
                if dt < cutoff:
                    conn.execute("""
                    UPDATE signals SET status='EXPIRED', resolved_at=?, accuracy=NULL
                    WHERE id=?
                    """, (utc_now(), sid))
            except Exception:
                pass

def performance() -> dict[str, int | float | None]:
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        calls = conn.execute("SELECT COUNT(*) FROM signals WHERE signal='CALL'").fetchone()[0]
        puts = conn.execute("SELECT COUNT(*) FROM signals WHERE signal='PUT'").fetchone()[0]
        wins = conn.execute("SELECT COUNT(*) FROM signals WHERE status='SUCCESS'").fetchone()[0]
        losses = conn.execute("SELECT COUNT(*) FROM signals WHERE status='FAILED'").fetchone()[0]
        expired = conn.execute("SELECT COUNT(*) FROM signals WHERE status='EXPIRED'").fetchone()[0]
        ambiguous = conn.execute("SELECT COUNT(*) FROM signals WHERE status='AMBIGUOUS'").fetchone()[0]
        resolved = wins + losses
        accuracy = round(wins / resolved * 100, 2) if resolved else None
        return {
            "total": total, "calls": calls, "puts": puts,
            "wins": wins, "losses": losses, "expired": expired,
            "ambiguous": ambiguous, "resolved": resolved,
            "accuracy_percent": accuracy
        }

def latest_signal() -> dict[str, Any] | None:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
        SELECT * FROM signals ORDER BY id DESC LIMIT 1
        """).fetchone()
        return dict(row) if row else None

def list_signals(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
        SELECT * FROM signals ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(x) for x in rows]

def insert_equity_signal(signal: dict[str, Any]) -> int | None:
    with connect() as conn:
        cur = conn.execute("""
        INSERT OR IGNORE INTO equity_signals
        (created_at,symbol,signal,technical_score,context_score,total_score,
         entry_price,target_price,stop_loss,expected_holding_minutes,
         signal_candle_time,reason,feature_snapshot_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            utc_now(), signal["symbol"], signal["signal"],
            signal["technical_score"], signal["context_score"],
            signal["total_score"], signal["entry_price"],
            signal["target_price"], signal["stop_loss"],
            signal.get("expected_holding_minutes"),
            signal["signal_candle_time"], signal["reason"],
            json.dumps(signal.get("feature_snapshot"), allow_nan=False)
            if signal.get("feature_snapshot") is not None else None,
        ))
        if cur.rowcount == 0:
            return None
        row_id = cur.lastrowid
        return int(row_id) if row_id is not None else None

def list_equity_training_rows(limit: int = 10000) -> list[dict[str, Any]]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
        SELECT id,created_at,symbol,technical_score,context_score,total_score,
               entry_price,target_price,stop_loss,expected_holding_minutes,
               signal_candle_time,feature_snapshot_json,status,result_price,resolved_at
        FROM equity_signals
        WHERE status IN ('TARGET_HIT','STOP_HIT') AND feature_snapshot_json IS NOT NULL
        ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]

def resolve_equity_signal(
    symbol: str,
    bar: dict[str, Any],
    bar_timestamp: datetime,
) -> None:
    timezone_info = ZoneInfo(settings.market_timezone)
    if bar_timestamp.tzinfo is None:
        bar_timestamp = bar_timestamp.replace(tzinfo=timezone_info)
    else:
        bar_timestamp = bar_timestamp.astimezone(timezone_info)

    with connect() as conn:
        rows = conn.execute("""
        SELECT id, target_price, stop_loss, signal_candle_time
        FROM equity_signals WHERE symbol=? AND status='OPEN'
        """, (symbol,)).fetchall()
        for row_id, target, stop, signal_candle_time in rows:
            signal_time = datetime.fromisoformat(signal_candle_time)
            if signal_time.tzinfo is None:
                signal_time = signal_time.replace(tzinfo=timezone_info)
            else:
                signal_time = signal_time.astimezone(timezone_info)

            if (
                signal_time.date() != bar_timestamp.date()
                or bar_timestamp - signal_time > timedelta(minutes=settings.signal_expiry_minutes)
            ):
                conn.execute("""
                UPDATE equity_signals SET status='EXPIRED', result_price=NULL, resolved_at=?
                WHERE id=?
                """, (utc_now(), row_id))
                continue

            target_hit = bar["high"] >= target
            stop_hit = bar["low"] <= stop
            if target_hit and stop_hit:
                status = "AMBIGUOUS"
            elif target_hit:
                status = "TARGET_HIT"
            elif stop_hit:
                status = "STOP_HIT"
            else:
                continue
            conn.execute("""
            UPDATE equity_signals SET status=?, result_price=?, resolved_at=?
            WHERE id=?
            """, (status, bar["close"], utc_now(), row_id))

def list_equity_signals(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
        SELECT * FROM equity_signals ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]

def list_equity_signals_by_status(
    status: Literal["OPEN", "STOP_HIT", "TARGET_HIT", "EXPIRED"],
    limit: int = 50,
) -> list[dict[str, Any]]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
        SELECT * FROM equity_signals WHERE status=? ORDER BY id DESC LIMIT ?
        """, (status, limit)).fetchall()
        return [dict(row) for row in rows]

def equity_signal_performance() -> dict[str, int | float | None]:
    with connect() as conn:
        rows = conn.execute("""
        SELECT status, COUNT(*), COUNT(DISTINCT symbol)
        FROM equity_signals GROUP BY status
        """).fetchall()

    counts = {
        status: (int(signal_count), int(stock_count))
        for status, signal_count, stock_count in rows
    }
    target_hits, target_stocks = counts.get("TARGET_HIT", (0, 0))
    stop_hits, stop_stocks = counts.get("STOP_HIT", (0, 0))
    resolved = target_hits + stop_hits
    accuracy = round(target_hits / resolved * 100, 2) if resolved else None
    return {
        "open_signals": counts.get("OPEN", (0, 0))[0],
        "target_hits": target_hits,
        "target_stocks": target_stocks,
        "stop_hits": stop_hits,
        "stop_stocks": stop_stocks,
        "ambiguous": counts.get("AMBIGUOUS", (0, 0))[0],
        "expired": counts.get("EXPIRED", (0, 0))[0],
        "resolved": resolved,
        "accuracy_percent": accuracy,
    }

def list_news(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
        SELECT * FROM news ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(x) for x in rows]

def list_candles(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
        SELECT id, timeframe, timestamp, open, high, low, close, volume,
               ema9, ema21, rsi14, vwap, atr14
        FROM candles ORDER BY timestamp DESC, id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(x) for x in rows]
