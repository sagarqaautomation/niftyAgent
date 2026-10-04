import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any

from config import settings

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def connect():
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    return sqlite3.connect(settings.db_path)

def init_db():
    with connect() as conn:
        with open("schema.sql", "r", encoding="utf-8") as f:
            conn.executescript(f.read())

def insert_candle(c: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute("""
        INSERT INTO candles
        (timeframe,timestamp,open,high,low,close,volume,ema9,ema21,rsi14,vwap,atr14)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(timeframe,timestamp) DO UPDATE SET
        open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
        volume=excluded.volume,ema9=excluded.ema9,ema21=excluded.ema21,
        rsi14=excluded.rsi14,vwap=excluded.vwap,atr14=excluded.atr14
        """, (
            c["timeframe"], c["timestamp"], c["open"], c["high"], c["low"],
            c["close"], c.get("volume", 0), c.get("ema9"), c.get("ema21"),
            c.get("rsi14"), c.get("vwap"), c.get("atr14")
        ))

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
         target_price,stop_loss,option_symbol,option_entry,reason,news_bias,option_bias)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            utc_now(), s["signal"], s["technical_score"], s["context_score"],
            s["total_score"], s.get("entry_price"), s.get("target_price"),
            s.get("stop_loss"), s.get("option_symbol"), s.get("option_entry"),
            s.get("reason"), s.get("news_bias"), s.get("option_bias")
        ))
        row_id = cur.lastrowid
        return int(row_id) if row_id is not None else 0

def resolve_with_bar(bar: dict[str, Any]) -> None:
    with connect() as conn:
        rows = conn.execute("""
        SELECT id, signal, entry_price, target_price, stop_loss, created_at
        FROM signals WHERE status='OPEN'
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

def list_news(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
        SELECT * FROM news ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(x) for x in rows]
