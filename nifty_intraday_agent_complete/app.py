import sqlite3
from datetime import datetime

import pandas as pd
import streamlit as st

from config import settings
from database import init_db, performance, latest_signal, list_signals, list_news

init_db()

st.set_page_config(page_title="Nifty Intraday Agent", page_icon="📈", layout="wide")

st.title("Nifty Intraday Agent")

status_col, refresh_col = st.columns([3, 1])
with status_col:
    st.caption(
        f"Mode: {'LIVE DATA' if settings.live_market_data else 'DEMO / NO LIVE DATA'} | "
        f"Paper trading: {'ON' if settings.paper_trading else 'OFF'}"
    )
with refresh_col:
    st.button("Refresh now")

try:
    with sqlite3.connect(settings.db_path) as conn:
        last_candle = conn.execute(
            "SELECT timestamp FROM candles ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_signal = conn.execute(
            "SELECT created_at FROM signals ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_news = conn.execute(
            "SELECT fetched_at FROM news ORDER BY id DESC LIMIT 1"
        ).fetchone()
except Exception:
    last_candle = None
    last_signal = None
    last_news = None

status_text = "LIVE" if settings.live_market_data else "DEMO"
if last_candle:
    st.success(f"Market data status: {status_text} | Last candle: {last_candle[0]}")
else:
    st.warning(f"Market data status: {status_text} | No candle data yet. Run the live engine or wait for market ticks.")

if last_signal:
    st.info(f"Latest signal timestamp: {last_signal[0]}")
if last_news:
    st.info(f"Latest news fetch time: {last_news[0]}")

perf = performance()
metrics = [
    ("Total", perf.get("total", 0)),
    ("Calls", perf.get("calls", 0)),
    ("Puts", perf.get("puts", 0)),
    ("Wins", perf.get("wins", 0)),
    ("Losses", perf.get("losses", 0)),
    ("Accuracy", f"{perf.get('accuracy_percent')}%" if perf.get("accuracy_percent") is not None else "N/A"),
]

cols = st.columns(len(metrics))
for col, (label, value) in zip(cols, metrics):
    col.metric(label, value)

st.markdown("---")

latest = latest_signal()
if latest:
    st.subheader("Latest Signal")
    st.json(latest)
else:
    st.info("No signal has been generated yet.")

st.markdown("---")

signals = list_signals(10)
signals_df = pd.DataFrame(signals)
if not signals_df.empty:
    display_cols = [
        "id", "created_at", "signal", "total_score", "entry_price",
        "target_price", "stop_loss", "status", "news_bias", "option_bias"
    ]
    display_cols = [c for c in display_cols if c in signals_df.columns]
    signals_df = signals_df[display_cols]
else:
    signals_df = pd.DataFrame(columns=["id", "created_at", "signal", "total_score", "status"])

news = list_news(10)
news_df = pd.DataFrame(news)
if not news_df.empty:
    news_cols = [c for c in ["id", "source", "title", "market_bias", "published_at"] if c in news_df.columns]
    news_df = news_df[news_cols]
else:
    news_df = pd.DataFrame(columns=["id", "source", "title", "market_bias", "published_at"])

left_col, right_col = st.columns(2)
with left_col:
    st.subheader("Recent Signals")
    st.dataframe(signals_df, use_container_width=True)

with right_col:
    st.subheader("Recent News")
    st.dataframe(news_df, use_container_width=True)

st.markdown("---")
st.caption(f"Last refresh: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
