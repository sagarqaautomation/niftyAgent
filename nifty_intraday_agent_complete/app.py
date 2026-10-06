from datetime import datetime, timezone, tzinfo
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from config import settings
from database import (
    init_db, performance, latest_signal, list_signals, list_news, list_spot_candles,
    get_market_status, list_equity_signals_by_status, equity_signal_performance,
    list_recent_equity_patterns,
)

init_db()

streamlit_ui = cast(Any, st)

streamlit_ui.set_page_config(page_title="Nifty Intraday Agent", page_icon="📈", layout="wide")

def _seconds_since(value: str | None, default_timezone: tzinfo) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_timezone)
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())


@st.fragment(run_every=1)
def render_live_status() -> None:
    feed = get_market_status()
    feed_state = feed.get("state", "WAITING") if feed else "WAITING"
    tick_age = _seconds_since(feed.get("last_tick_at"), timezone.utc) if feed else None
    spot_age = (
        _seconds_since(feed.get("spot_last_tick_at"), ZoneInfo(settings.market_timezone))
        if feed else None
    )
    spot_price = feed.get("spot_last_price") if feed else None
    one_minute_bars = feed.get("one_minute_bars", 0) if feed else 0
    five_minute_bars = feed.get("five_minute_bars", 0) if feed else 0
    latest_candles = list_spot_candles(1)
    candle_time = latest_candles[0].get("timestamp") if latest_candles else None
    candle_age = (
        _seconds_since(candle_time, ZoneInfo(settings.market_timezone))
        if candle_time else None
    )

    status_cols = st.columns(4)
    status_delta = (
        "Session closed"
        if feed_state == "CLOSED"
        else f"{tick_age:.0f}s since tick" if tick_age is not None else "No tick recorded"
    )
    status_cols[0].metric(
        "Market feed",
        feed_state,
        status_delta,
        delta_color="off",
    )
    status_cols[1].metric(
        "NIFTY spot",
        f"{float(spot_price):,.2f}" if spot_price is not None else "Unavailable",
    )
    status_cols[2].metric(
        "Signal warm-up",
        f"{min(one_minute_bars, 30)}/30 · {min(five_minute_bars, 30)}/30",
    )
    status_cols[3].metric("Last spot candle", str(candle_time or "No spot candle"))

    if feed and feed_state in {"ERROR", "DISCONNECTED"}:
        st.error(feed.get("last_error") or f"Market feed {feed_state.lower()}.")
    elif feed_state == "CLOSED":
        st.info("NSE session closed; live market data has been stopped.")
    elif feed_state == "LIVE" and tick_age is not None and tick_age > 10:
        st.warning(f"Market feed is stale; last tick was {tick_age:.0f} seconds ago.")
    elif feed_state in {"STARTING", "CONNECTED"}:
        st.info(f"Market feed {feed_state.lower()}.")
    if feed and feed.get("last_error") and feed_state not in {"ERROR", "DISCONNECTED"}:
        st.warning(f"Market data notice: {feed['last_error']}")
    if feed_state != "CLOSED" and candle_age is not None and candle_age > 120:
        st.warning(f"Latest stored NIFTY spot candle is stale by {candle_age / 60:.1f} minutes.")
    if feed_state != "CLOSED" and spot_age is not None and spot_age > 10:
        st.warning(f"NIFTY spot reference is stale ({spot_age:.0f}s old).")

    analysis_state = feed.get("analysis_state") if feed else None
    analysis_reason = feed.get("analysis_reason") if feed else None
    analysis_score = feed.get("analysis_score") if feed else None
    score_text = (
        f" · score {analysis_score}/{settings.min_total_score}"
        if analysis_score is not None else ""
    )
    if analysis_state in {"CALL", "PUT"}:
        st.success(f"Latest NIFTY evaluation: {analysis_state}{score_text} · {analysis_reason}")
    elif analysis_state in {"BLOCKED", "ERROR"}:
        st.error(f"Signal analysis {analysis_state.lower()}: {analysis_reason}")
    elif analysis_state and analysis_state.endswith("_ALERT_NOT_SENT"):
        st.info(f"{analysis_state.split('_', 1)[0]} signal was saved. See the Signals tab.")
    elif analysis_state in {"CALL_TRIGGERED", "PUT_TRIGGERED"}:
        st.info(f"{analysis_state.removesuffix('_TRIGGERED')} setup already triggered; waiting for reset.")
    elif analysis_state == "WARMING_UP":
        st.info(f"Signal analysis warming up: {analysis_reason}")
    elif analysis_state == "CLOSED":
        st.info(analysis_reason or "NSE session closed.")
    elif analysis_state:
        st.info(f"Latest NIFTY evaluation: {analysis_state}{score_text} · {analysis_reason}")
    st.caption(
        f"Signal rule: score {settings.min_total_score}/7 on a closed candle; "
        "volume confirms the setup internally."
    )


@st.fragment(run_every=5)
def render_overview() -> None:
    perf = performance()
    metrics: list[tuple[str, int | float | str | None]] = [
        ("Total signals", perf.get("total", 0)),
        ("CALL", perf.get("calls", 0)),
        ("PUT", perf.get("puts", 0)),
        ("Wins", perf.get("wins", 0)),
        ("Losses", perf.get("losses", 0)),
        (
            "Accuracy",
            f"{perf.get('accuracy_percent')}%"
            if perf.get("accuracy_percent") is not None else "N/A",
        ),
    ]
    metric_cols = st.columns(len(metrics))
    for column, (label, value) in zip(metric_cols, metrics):
        column.metric(label, value)

    latest = latest_signal()
    st.subheader("Latest Signal")
    if latest:
        is_spot_signal = latest.get("signal_instrument") == "NIFTY spot index"
        if not is_spot_signal:
            st.caption(
                "Historical alert uses a different instrument; its levels are not NIFTY spot levels."
            )
        signal_cols = st.columns(4)
        signal_cols[0].metric("Direction", latest.get("signal", "N/A"))
        signal_cols[1].metric("Entry", latest.get("entry_price", "N/A"))
        signal_cols[2].metric("Target", latest.get("target_price", "N/A"))
        signal_cols[3].metric("Stop loss", latest.get("stop_loss", "N/A"))
        st.caption(
            f"{latest.get('signal_instrument') or 'Instrument unavailable'} · "
            f"{latest.get('created_at') or 'Time unavailable'} · "
            f"Score {latest.get('total_score', 'N/A')}"
        )
        st.write(latest.get("reason") or "No signal reason recorded.")
    else:
        st.info("No signal has been recorded yet.")
    st.caption("Performance includes historical alerts created before spot-only output was enabled.")


@st.fragment(run_every=5)
def render_signals() -> None:
    signals_df = pd.DataFrame(list_signals(20))
    st.subheader("Recent Signals")
    st.caption("The instrument column identifies the price basis for each entry, target, and stop.")
    if not signals_df.empty:
        display_cols = [
            "created_at", "signal", "technical_score", "context_score", "total_score",
            "entry_price", "target_price", "stop_loss", "signal_instrument",
            "signal_candle_time", "status", "reason",
        ]
        signals_df = signals_df[[column for column in display_cols if column in signals_df.columns]]
        streamlit_ui.dataframe(signals_df, width="stretch", hide_index=True)
    else:
        st.info("No NIFTY CALL/PUT signals have fired yet.")

    st.subheader("Recent NIFTY Spot Candles")
    candles_df = pd.DataFrame(list_spot_candles(20))
    if not candles_df.empty:
        streamlit_ui.dataframe(candles_df, width="stretch", hide_index=True)
    else:
        st.info("No NIFTY spot candles have been stored yet.")


@st.fragment(run_every=5)
def render_equities() -> None:
    st.subheader("Recent Equity BUY Signals")
    st.caption(
        "Holding time extrapolates recent one-minute price movement; it is not a forecast or guarantee."
    )
    if settings.equity_scan_enabled:
        equity_df = pd.DataFrame(list_equity_signals_by_status("OPEN", 50))
        if not equity_df.empty:
            equity_cols = [
                "created_at", "symbol", "signal", "technical_score", "context_score",
                "total_score", "entry_price", "target_price", "stop_loss",
                "expected_holding_minutes", "signal_candle_time", "status", "reason",
            ]
            equity_df = equity_df[[column for column in equity_cols if column in equity_df.columns]]
            streamlit_ui.dataframe(equity_df, width="stretch", hide_index=True)
        else:
            st.info(
                "No open equity BUY setups right now. The scanner continues "
                "evaluating the enabled watchlist; resolved hits are shown below."
            )

        stop_col, target_col = streamlit_ui.columns(2)
        outcome_cols = [
            "resolved_at", "symbol", "entry_price", "stop_loss", "target_price",
            "result_price", "total_score",
        ]
        with stop_col:
            st.subheader("Stop Loss Hit")
            stop_df = pd.DataFrame(
                list_equity_signals_by_status("STOP_HIT", 50)
            )
            if not stop_df.empty:
                stop_df = stop_df[[column for column in outcome_cols if column in stop_df.columns]]
                stop_df = stop_df.rename(columns={
                    "resolved_at": "Resolved at (UTC)",
                    "result_price": "Hit bar close",
                    "total_score": "Score",
                })
                streamlit_ui.dataframe(stop_df, width="stretch", hide_index=True)
            else:
                st.info("No stop losses have been hit.")

        with target_col:
            st.subheader("Target Hit")
            target_df = pd.DataFrame(
                list_equity_signals_by_status("TARGET_HIT", 50)
            )
            if not target_df.empty:
                target_df = target_df[[column for column in outcome_cols if column in target_df.columns]]
                target_df = target_df.rename(columns={
                    "resolved_at": "Resolved at (UTC)",
                    "result_price": "Hit bar close",
                    "total_score": "Score",
                })
                streamlit_ui.dataframe(target_df, width="stretch", hide_index=True)
            else:
                st.info("No targets have been hit.")

        st.subheader("Expired Signals")
        expired_df = pd.DataFrame(
            list_equity_signals_by_status("EXPIRED", 50)
        )
        if not expired_df.empty:
            expired_cols = [
                "resolved_at", "symbol", "entry_price", "stop_loss", "target_price",
                "expected_holding_minutes", "signal_candle_time", "total_score",
            ]
            expired_df = expired_df[[column for column in expired_cols if column in expired_df.columns]]
            expired_df = expired_df.rename(columns={
                "resolved_at": "Expired at (UTC)",
                "expected_holding_minutes": "Estimated hold (min)",
                "total_score": "Score",
            })
            streamlit_ui.dataframe(expired_df, width="stretch", hide_index=True)
        else:
            st.info("No equity signals have expired.")

        st.subheader("Recent Candlestick Patterns")
        pattern_df = pd.DataFrame(list_recent_equity_patterns(100))
        if not pattern_df.empty:
            pattern_cols = ["timestamp", "symbol", "timeframe", "pattern_names"]
            pattern_df = pattern_df[
                [column for column in pattern_cols if column in pattern_df.columns]
            ].rename(columns={
                "timestamp": "Candle time",
                "pattern_names": "Detected patterns",
            })
            streamlit_ui.dataframe(pattern_df, width="stretch", hide_index=True)
        else:
            st.info("No candlestick patterns detected in stored equity candles yet.")
        st.caption(
            "Shadow analysis only: patterns do not change BUY decisions until backtested."
        )

        equity_performance = equity_signal_performance()
        accuracy = equity_performance["accuracy_percent"]
        summary_df = pd.DataFrame([
            {"Metric": "Open BUY setups", "Value": str(equity_performance["open_signals"])},
            {"Metric": "Target-hit signals", "Value": str(equity_performance["target_hits"])},
            {"Metric": "Unique target-hit stocks", "Value": str(equity_performance["target_stocks"])},
            {"Metric": "Stop-loss signals", "Value": str(equity_performance["stop_hits"])},
            {"Metric": "Unique stop-loss stocks", "Value": str(equity_performance["stop_stocks"])},
            {"Metric": "Ambiguous outcomes", "Value": str(equity_performance["ambiguous"])},
            {"Metric": "Expired signals", "Value": str(equity_performance["expired"])},
            {"Metric": "Resolved signals", "Value": str(equity_performance["resolved"])},
            {"Metric": "Accuracy", "Value": f"{accuracy}%" if accuracy is not None else "N/A"},
        ])
        st.subheader("Equity Outcome Summary")
        streamlit_ui.dataframe(summary_df, width="stretch", hide_index=True)
        st.caption(
            "Accuracy = target-hit signals / (target-hit + stop-loss signals); "
            "ambiguous, expired, and open signals are excluded."
        )
        st.caption(f"Watchlist: {settings.equity_watchlist_path}")
    else:
        st.info("Equity scanning is disabled. Set EQUITY_SCAN_ENABLED=true to enable it.")


@st.fragment(run_every=60)
def render_news() -> None:
    st.subheader("Recent News")
    news_df = pd.DataFrame(list_news(20))
    if not news_df.empty:
        news_cols = ["source", "title", "market_bias", "published_at"]
        news_df = news_df[[column for column in news_cols if column in news_df.columns]]
        streamlit_ui.dataframe(news_df, width="stretch", hide_index=True)
    else:
        st.info("No news items have been recorded yet.")


def render_dashboard() -> None:
    st.title("Nifty Intraday Agent")

    header_col, refresh_col = st.columns([4, 1])
    with header_col:
        st.caption(
            f"{'LIVE DATA' if settings.live_market_data else 'DEMO / NO LIVE DATA'} · "
            f"Paper trading {'ON' if settings.paper_trading else 'OFF'}"
        )
    with refresh_col:
        st.button("Refresh now")

    render_live_status()
    overview_tab, signals_tab, equities_tab, news_tab = streamlit_ui.tabs(
        ["Overview", "Signals", "Equities", "News"]
    )
    with overview_tab:
        render_overview()
    with signals_tab:
        render_signals()
    with equities_tab:
        render_equities()
    with news_tab:
        render_news()

    st.caption(f"Updated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


render_dashboard()
