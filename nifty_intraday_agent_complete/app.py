import json
from pathlib import Path
from datetime import datetime, timezone, tzinfo
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from config import settings
from market_hours import is_market_open
from database import (
    init_db, performance, latest_signal, list_signals, list_news, list_spot_candles,
    get_market_status, list_equity_signals_by_status, equity_signal_performance,
    list_recent_equity_patterns, list_equity_scan_status, list_equity_research_candidates,
)
from indicators import detect_latest_candlestick_patterns

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
    market_open = is_market_open()
    tick_age = _seconds_since(feed.get("last_tick_at"), timezone.utc) if feed else None
    spot_age = (
        _seconds_since(feed.get("spot_last_tick_at"), ZoneInfo(settings.market_timezone))
        if feed else None
    )
    spot_price = feed.get("spot_last_price") if feed else None
    kite_auth_state = feed.get("kite_auth_state", "UNKNOWN") if feed else "UNKNOWN"
    kite_auth_reason = feed.get("kite_auth_reason") if feed else None
    kite_auth_checked_at = feed.get("kite_auth_checked_at") if feed else None
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
        f"Feed {feed_state} · {tick_age:.0f}s since tick"
        if tick_age is not None
        else f"Feed {feed_state}"
    ) if market_open else "Weekdays 09:00-15:30 IST"
    status_cols[0].metric(
        "NSE market",
        "LIVE" if market_open else "CLOSED",
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

    if kite_auth_state == "SUCCESS":
        auth_checked = f" · checked {kite_auth_checked_at}" if kite_auth_checked_at else ""
        st.success(f"Kite authentication: SUCCESS — API key/access token accepted{auth_checked}.")
    elif kite_auth_state == "FAILED":
        st.error(f"Kite authentication: FAILED — {kite_auth_reason or 'Check Kite credentials and access token.'}")
    else:
        st.info("Kite authentication: NOT VERIFIED yet. The worker must complete the Kite profile check.")

    if not market_open:
        st.info("NSE market is closed. Session hours: weekdays 09:00-15:30 IST.")
    elif feed and feed_state in {"ERROR", "DISCONNECTED"}:
        st.error(feed.get("last_error") or f"Market feed {feed_state.lower()}.")
    elif feed_state == "RECONNECTING":
        st.warning(
            (feed.get("last_error") or "Kite WebSocket connection dropped.")
            + " Automatic reconnection is in progress; do not rely on new signals until fresh ticks resume."
        )
    elif feed_state == "CLOSED":
        st.info("NSE session is open, but the live market-data feed is stopped.")
    elif feed_state == "LIVE" and tick_age is not None and tick_age > 10:
        st.warning(f"Market feed is stale; last tick was {tick_age:.0f} seconds ago.")
    elif feed_state in {"STARTING", "CONNECTED"}:
        st.info(f"Market feed {feed_state.lower()}.")
    # Only display the stored connection error while the feed is unhealthy.
    # A reconnect can briefly leave a previous error string in older/local DB
    # state; do not show a stale 1006 banner once the feed is CONNECTED or LIVE.
    if feed and feed.get("last_error") and feed_state in {"ERROR", "DISCONNECTED", "RECONNECTING"}:
        st.warning(f"Market data notice: {feed['last_error']}")
    if market_open and candle_age is not None and candle_age > 120:
        st.warning(f"Latest stored NIFTY spot candle is stale by {candle_age / 60:.1f} minutes.")
    if market_open and spot_age is not None and spot_age > 10:
        st.warning(f"NIFTY spot reference is stale ({spot_age:.0f}s old).")

    analysis_state = feed.get("analysis_state") if feed else None
    analysis_reason = feed.get("analysis_reason") if feed else None
    analysis_score = feed.get("analysis_score") if feed else None
    score_text = (
        f" · score {analysis_score}/{settings.min_total_score}"
        if analysis_score is not None else ""
    )
    if market_open:
        if analysis_state in {"CALL", "PUT"}:
            st.success(f"Latest NIFTY evaluation: {analysis_state}{score_text} · {analysis_reason}")
        elif analysis_state in {"BLOCKED", "ERROR"}:
            st.error(f"Signal analysis {analysis_state.lower()}: {analysis_reason}")
        elif analysis_state and analysis_state.endswith("_ALERT_NOT_SENT"):
            st.info(f"{analysis_state.split('_', 1)[0]} signal was saved. See the Signals tab.")
        elif analysis_state in {"CALL_TRIGGERED", "PUT_TRIGGERED"}:
            direction = analysis_state.removesuffix("_TRIGGERED")
            if tick_age is not None and tick_age > 10:
                st.warning(
                    f"{direction} setup lock is active, but the market feed is stale "
                    f"({tick_age:.0f}s since the last tick). A SUCCESS result in Recent Signals "
                    "does not automatically reset the setup; fresh candles are needed to evaluate "
                    "the reset condition. Restore the live feed before relying on new signals."
                )
            else:
                st.info(
                    f"{direction} setup lock is active. A SUCCESS result in Recent Signals "
                    "records that signal's outcome; it does not automatically reset the setup. "
                    "The strategy waits for its reset condition before alerting again."
                )
        elif analysis_state == "WARMING_UP":
            st.info(f"Signal analysis warming up: {analysis_reason}")
        elif analysis_state == "CLOSED":
            st.info("NSE session is open; waiting for signal analysis.")
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
        (
            "Average R",
            perf.get("avg_r") if perf.get("avg_r") is not None else "N/A",
        ),
        (
            "Profit factor",
            perf.get("profit_factor") if perf.get("profit_factor") is not None else "N/A",
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
        streamlit_ui.dataframe(signals_df, use_container_width=True, hide_index=True)
    else:
        st.info("No NIFTY CALL/PUT signals have fired yet.")

    st.subheader("Recent NIFTY Spot Candles")
    candles_df = pd.DataFrame(list_spot_candles(20))
    if not candles_df.empty:
        streamlit_ui.dataframe(candles_df, use_container_width=True, hide_index=True)
    else:
        st.info("No NIFTY spot candles have been stored yet.")


def add_research_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Expose research verdict, analyst upside and reward/risk stored in each
    signal's feature snapshot."""
    verdicts: list[str | None] = []
    scores: list[int | None] = []
    upsides: list[float | None] = []
    summaries: list[str | None] = []
    ratios: list[float | None] = []
    snapshots = df["feature_snapshot_json"] if "feature_snapshot_json" in df.columns else [None] * len(df)
    for raw in snapshots:
        try:
            snapshot = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            snapshot = {}
        research = snapshot.get("research") or {}
        verdicts.append(research.get("verdict"))
        scores.append(research.get("score"))
        upsides.append((research.get("analysts") or {}).get("avg_target_upside_pct"))
        notes = (research.get("fundamentals") or {}).get("notes") or []
        headlines = (research.get("news") or {}).get("headlines") or []
        parts = headlines[:1] + notes
        summaries.append("; ".join(parts) if parts else None)
        ratios.append(snapshot.get("reward_risk"))
    out = df.copy()
    out["research_verdict"] = verdicts
    out["research_score"] = scores
    out["analyst_upside_pct"] = upsides
    out["research_summary"] = summaries
    out["reward_risk"] = ratios
    return out


@st.fragment(run_every=5)
def render_patterns() -> None:
    market_timezone = ZoneInfo(settings.market_timezone)
    now = pd.Timestamp.now(tz=market_timezone)
    nifty_patterns: list[dict[str, str]] = []
    spot_df = pd.DataFrame(list_spot_candles(200))
    if not spot_df.empty:
        for timeframe, duration_minutes in (("1min", 1), ("5min", 5)):
            frame = spot_df.loc[spot_df["timeframe"] == timeframe].copy()
            if frame.empty:
                continue
            candle_times = pd.DatetimeIndex(
                pd.to_datetime(frame["timestamp"], errors="coerce")
            )
            candle_times = (
                candle_times.tz_localize(market_timezone)
                if candle_times.tz is None
                else candle_times.tz_convert(market_timezone)
            )
            frame.index = candle_times
            frame = frame.loc[
                frame.index + pd.Timedelta(minutes=duration_minutes) <= now
            ].sort_index().tail(100)
            patterns = detect_latest_candlestick_patterns(frame)
            if frame.empty or not patterns:
                continue
            candle_time = frame.index[-1].isoformat()
            nifty_patterns.extend({
                "Timeframe": timeframe,
                "Closed candle": candle_time,
                "Pattern": pattern["name"],
                "Direction": pattern["direction"],
            } for pattern in patterns)

    st.subheader("NIFTY")
    st.caption("Patterns are confirmed on the latest fully closed candle.")
    if nifty_patterns:
        streamlit_ui.dataframe(
            pd.DataFrame(nifty_patterns), use_container_width=True, hide_index=True
        )
    else:
        st.info("No pattern detected on the latest closed NIFTY candles.")

    st.subheader("Equities")
    equity_patterns: list[dict[str, str]] = []
    for item in list_recent_equity_patterns(300):
        try:
            candle_time = pd.Timestamp(item["timestamp"])
            candle_time = (
                candle_time.tz_localize(market_timezone)
                if candle_time.tzinfo is None
                else candle_time.tz_convert(market_timezone)
            )
        except (KeyError, TypeError, ValueError):
            continue
        age = now - candle_time
        if age < pd.Timedelta(0) or age > pd.Timedelta(minutes=6):
            continue
        for pattern in item.get("patterns", []):
            equity_patterns.append({
                "Symbol": str(item.get("symbol", "")),
                "Timeframe": str(item.get("timeframe", "")),
                "Closed candle": candle_time.isoformat(),
                "Pattern": str(pattern.get("name", "")),
                "Direction": str(pattern.get("direction", "")),
            })
    if equity_patterns:
        streamlit_ui.dataframe(
            pd.DataFrame(equity_patterns), use_container_width=True, hide_index=True
        )
    else:
        st.info("No equity patterns detected in the last six minutes.")
    st.caption("Pattern detections are informational and never decide whether an equity BUY is generated.")


@st.fragment(run_every=5)
def render_equities() -> None:
    market_day = datetime.now(ZoneInfo(settings.market_timezone)).date()
    st.subheader("Equity Research Learning")
    learning_report_path = Path("data/equity_live_learning_report.json")
    if learning_report_path.exists():
        try:
            learning_report = json.loads(learning_report_path.read_text(encoding="utf-8"))
            learning_status = str(learning_report.get("status", "UNKNOWN"))
            st.write(
                f"Status: {learning_status} · "
                f"Resolved outcomes: {learning_report.get('resolved_samples', 0)} · "
                f"Active model changed: {learning_report.get('active_model_changed', False)}"
            )
            holdout = learning_report.get("holdout") or {}
            if holdout:
                st.write(
                    f"Chronological holdout: {holdout.get('samples', 0)} outcomes · "
                    f"win rate: {holdout.get('win_rate_pct', 'N/A')}% · "
                    f"Brier improvement vs baseline: "
                    f"{holdout.get('brier_improvement_vs_baseline', 'N/A')}"
                )
        except (OSError, ValueError, TypeError) as exc:
            st.warning(f"Could not read equity learning report: {exc}")
    else:
        st.info(
            "No equity learning report yet. The learner needs at least 100 resolved "
            "equity target/stop outcomes with feature snapshots before it can evaluate a candidate."
        )

    st.subheader("Live Equity Scanner Diagnostics")
    scanner_rows = list_equity_scan_status(200)
    if scanner_rows:
        scanner_df = pd.DataFrame(scanner_rows)
        scanner_display_cols = [
            "symbol", "last_price", "last_tick_at", "one_minute_bars",
            "five_minute_bars", "last_evaluated_at", "rejection_reason",
        ]
        scanner_df = scanner_df[
            [column for column in scanner_display_cols if column in scanner_df.columns]
        ].rename(columns={
            "symbol": "Stock",
            "last_price": "Latest price",
            "last_tick_at": "Last tick",
            "one_minute_bars": "1m bars",
            "five_minute_bars": "5m bars",
            "last_evaluated_at": "Last evaluation",
            "rejection_reason": "Scanner status / rejection reason",
        })
        streamlit_ui.dataframe(scanner_df, use_container_width=True, hide_index=True)
    else:
        st.info(
            "No per-stock scanner diagnostics have been recorded yet. "
            "Check the worker log for watchlist resolution and subscription errors."
        )

    st.subheader("Recent Equity BUY Signals")
    st.caption(
        "Equity BUY signals are driven by news and analyst catalysts, company fundamentals "
        "and valuation, then confirmed by live price, volume and market context. "
        "Candlestick patterns are optional and never decide a signal. "
        "Holding time extrapolates recent one-minute price movement; it is not a forecast or guarantee."
    )
    if settings.equity_scan_enabled:
        st.subheader("News & Research Candidates")
        st.caption(
            "Stocks picked from fresh company news and analyst calls, from your watchlist or "
            "discovered across all NSE stocks (\"News discovery\"). Only SUPPORTIVE stocks can "
            "become BUY signals, and only after live price, volume and risk checks pass."
        )
        candidates = list_equity_research_candidates(100)
        if candidates:
            candidate_df = pd.DataFrame(candidates)[[
                "symbol", "source", "verdict", "research_score", "news_score", "analyst_score",
                "fundamentals_score", "analyst_upside_pct", "catalyst", "basis", "updated_at",
            ]].rename(columns={
                "symbol": "Stock",
                "source": "Found via",
                "verdict": "Research verdict",
                "research_score": "Research score",
                "news_score": "News",
                "analyst_score": "Analysts",
                "fundamentals_score": "Fundamentals",
                "analyst_upside_pct": "Analyst upside %",
                "catalyst": "Catalyst",
                "basis": "Research basis",
                "updated_at": "Updated (UTC)",
            })
            streamlit_ui.dataframe(candidate_df, use_container_width=True, hide_index=True)
        else:
            st.info(
                "No stocks have a fresh positive news or analyst catalyst right now. "
                "The list refreshes with each news update."
            )

        equity_df = pd.DataFrame(list_equity_signals_by_status("OPEN", 50))
        if not equity_df.empty:
            equity_df = add_research_columns(equity_df)
            equity_cols = [
                "created_at", "symbol", "signal", "research_verdict", "research_score",
                "analyst_upside_pct", "technical_score", "context_score",
                "total_score", "entry_price", "target_price", "stop_loss",
                "reward_risk", "expected_holding_minutes", "signal_candle_time",
                "status", "research_summary", "reason",
            ]
            equity_df = equity_df[[column for column in equity_cols if column in equity_df.columns]]
            equity_df = equity_df.rename(columns={
                "research_verdict": "Research",
                "research_score": "Research score",
                "analyst_upside_pct": "Analyst upside %",
                "reward_risk": "Reward/Risk",
                "research_summary": "Research basis",
            })
            streamlit_ui.dataframe(equity_df, use_container_width=True, hide_index=True)
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
            st.subheader("Stop Loss Hit Today")
            stop_df = pd.DataFrame(
                list_equity_signals_by_status(
                    "STOP_HIT", 50, market_day=market_day
                )
            )
            if not stop_df.empty:
                stop_df = stop_df[[column for column in outcome_cols if column in stop_df.columns]]
                stop_df = stop_df.rename(columns={
                    "resolved_at": "Resolved at (UTC)",
                    "result_price": "Hit bar close",
                    "total_score": "Score",
                })
                streamlit_ui.dataframe(stop_df, use_container_width=True, hide_index=True)
            else:
                st.info("No stop losses have been hit.")

        with target_col:
            st.subheader("Target Hit Today")
            target_df = pd.DataFrame(
                list_equity_signals_by_status(
                    "TARGET_HIT", 50, market_day=market_day
                )
            )
            if not target_df.empty:
                target_df = target_df[[column for column in outcome_cols if column in target_df.columns]]
                target_df = target_df.rename(columns={
                    "resolved_at": "Resolved at (UTC)",
                    "result_price": "Hit bar close",
                    "total_score": "Score",
                })
                streamlit_ui.dataframe(target_df, use_container_width=True, hide_index=True)
            else:
                st.info("No targets have been hit.")

        st.subheader("Expired Signals Today")
        expired_df = pd.DataFrame(
            list_equity_signals_by_status(
                "EXPIRED", 50, market_day=market_day
            )
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
            streamlit_ui.dataframe(expired_df, use_container_width=True, hide_index=True)
        else:
            st.info("No equity signals have expired.")

        st.subheader("Recent Candlestick Patterns (optional)")
        pattern_df = pd.DataFrame(list_recent_equity_patterns(100))
        if not pattern_df.empty:
            pattern_cols = ["timestamp", "symbol", "timeframe", "pattern_names"]
            pattern_df = pattern_df[
                [column for column in pattern_cols if column in pattern_df.columns]
            ].rename(columns={
                "timestamp": "Candle time",
                "pattern_names": "Detected patterns",
            })
            streamlit_ui.dataframe(pattern_df, use_container_width=True, hide_index=True)
        else:
            st.info("No candlestick patterns detected in stored equity candles yet.")
        st.caption(
            "Supporting information only: patterns add at most one optional score point and never block or trigger a BUY."
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
        streamlit_ui.dataframe(summary_df, use_container_width=True, hide_index=True)
        st.caption(
            "Outcome lists show this market day only; the summary retains all historical results."
        )
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
    news_df = pd.DataFrame(list_news(limit=50))
    if not news_df.empty:
        page_size = 10
        page_count = max(1, (len(news_df) + page_size - 1) // page_size)
        page_key = "news_page"
        current_page = min(
            max(int(st.session_state.get(page_key, 1)), 1),
            page_count,
        )
        st.session_state[page_key] = current_page
        selected_page = int(streamlit_ui.number_input(
            "News page",
            min_value=1,
            max_value=page_count,
            step=1,
            key=page_key,
        ))
        start = (selected_page - 1) * page_size
        visible_news = news_df.iloc[start:start + page_size].copy()
        news_cols = [
            "source", "title", "event_categories", "symbols",
            "market_bias", "published_at", "url",
        ]
        visible_news = visible_news[
            [column for column in news_cols if column in visible_news.columns]
        ]
        for column in ("event_categories", "symbols"):
            if column in visible_news.columns:
                fallback = "Unclassified" if column == "event_categories" else "Not matched"
                visible_news[column] = visible_news[column].map(
                    lambda values: ", ".join(cast(list[str], values))
                    if isinstance(values, list) and values
                    else fallback
                )
        streamlit_ui.dataframe(
            visible_news,
            use_container_width=True,
            hide_index=True,
            column_config={
                "url": streamlit_ui.column_config.LinkColumn(
                    "Article", display_text="Open"
                )
            },
        )
        first_item = start + 1
        last_item = min(start + page_size, len(news_df))
        st.caption(
            f"Showing newest items {first_item}-{last_item} of {len(news_df)} · "
            f"Page {selected_page} of {page_count}"
        )
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
    overview_tab, signals_tab, equities_tab, patterns_tab, news_tab = streamlit_ui.tabs(
        ["Overview", "Signals", "Equities", "Patterns", "News"]
    )
    with overview_tab:
        render_overview()
    with signals_tab:
        render_signals()
    with equities_tab:
        render_equities()
    with patterns_tab:
        render_patterns()
    with news_tab:
        render_news()

    st.caption(f"Updated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


render_dashboard()
