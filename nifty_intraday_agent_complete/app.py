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
    get_market_status, list_equity_signals, list_equity_signals_by_status,
    equity_signal_performance, list_recent_equity_patterns, list_equity_scan_status,
    list_equity_research_candidates,
)
from indicators import detect_latest_candlestick_patterns
from intraday_recommendations import collect_intraday_recommendations, load_stock_aliases

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


def render_equity_evidence(rows: list[dict[str, Any]]) -> None:
    st.subheader("Signal Evidence Trail")
    st.caption(
        "Each record keeps the decision checks, market-data timestamps, and matched article links "
        "available when the signal was created. Feed bylines are shown as supplied by the publisher."
    )
    rendered = 0
    for row in rows:
        try:
            snapshot = json.loads(row.get("feature_snapshot_json") or "{}")
        except (TypeError, ValueError):
            snapshot = {}
        audit = snapshot.get("audit_trail") or {}
        research = snapshot.get("research") or {}
        evidence = audit.get("source_evidence") or research.get("evidence") or []
        checks = audit.get("checks") or []
        if not audit and not evidence and not checks:
            continue
        rendered += 1
        label = (
            f"{row.get('symbol', 'Unknown')} · {row.get('status', 'UNKNOWN')} · "
            f"{row.get('created_at', 'time unavailable')}"
        )
        with st.expander(label):
            st.caption(
                f"1-minute candles through {audit.get('one_minute_candles_as_of') or row.get('signal_candle_time') or 'unknown'} · "
                f"5-minute candles through {audit.get('five_minute_candles_as_of') or 'unknown'} · "
                f"live price as of {audit.get('price_as_of') or 'unknown'}"
            )
            if checks:
                checks_df = pd.DataFrame(checks)
                streamlit_ui.dataframe(checks_df, use_container_width=True, hide_index=True)
            if evidence:
                evidence_df = pd.DataFrame(evidence)
                evidence_cols = [
                    "evidence_type", "source", "author", "analyst_recommendation",
                    "target_price", "target_upside_pct", "title", "published_at", "url",
                ]
                evidence_df = evidence_df[
                    [column for column in evidence_cols if column in evidence_df.columns]
                ].rename(columns={
                    "evidence_type": "Evidence type",
                    "source": "Publisher",
                    "author": "Feed byline",
                    "analyst_recommendation": "Call tag",
                    "target_price": "Target price",
                    "target_upside_pct": "Target upside %",
                    "published_at": "Published",
                    "title": "Article",
                })
                link_column = "url" if "url" in evidence_df.columns else None
                if link_column:
                    evidence_df = evidence_df.rename(columns={"url": "link"})
                streamlit_ui.dataframe(
                    evidence_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "link": streamlit_ui.column_config.LinkColumn(
                            "Source link", display_text="Open"
                        )
                    } if link_column else None,
                )
            else:
                st.info("No source article was attached to this signal snapshot.")
    if not rendered:
        st.info("New signals will show their decision checks and article evidence here.")


@st.fragment(run_every=5)
def render_equities() -> None:
    market_day = datetime.now(ZoneInfo(settings.market_timezone)).date()
    st.markdown(
        """
        <div class="section-header">
          <div><div class="section-kicker">MARKET INTELLIGENCE</div>
          <h2 style="margin:0">Equity workspace</h2></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "Research-led intraday setups across NSE equities. News and analyst evidence identify candidates; "
        "live price, volume, and risk checks confirm setups. Candlestick patterns are optional context only."
    )
    if not settings.equity_scan_enabled:
        st.info("Equity scanning is disabled. Set EQUITY_SCAN_ENABLED=true to enable it.")
        return

    performance = equity_signal_performance(25.0)
    candidates = list_equity_research_candidates(200)
    open_rows = list_equity_signals_by_status("OPEN", 200)
    stop_rows = list_equity_signals_by_status("STOP_HIT", 200, market_day=market_day)
    target_rows = list_equity_signals_by_status("TARGET_HIT", 200, market_day=market_day)
    expired_rows = list_equity_signals_by_status("EXPIRED", 200, market_day=market_day)
    scanner_rows = list_equity_scan_status(200)

    metric_cols = streamlit_ui.columns(5)
    metric_values = [
        ("Research candidates", len(candidates), "Fresh catalysts"),
        ("Active setups", len(open_rows), "Open BUY signals"),
        ("Targets today", len(target_rows), "Resolved today"),
        ("Stops today", len(stop_rows), "Resolved today"),
        ("Net win rate", f"{performance['net_win_rate_percent']}%" if performance["net_win_rate_percent"] is not None else "—", f"After 25 bps · {performance['resolved']} resolved"),
    ]
    for col, (label, value, note) in zip(metric_cols, metric_values):
        with col:
            st.metric(label, value, help=note)

    overview_tab, setups_tab, outcomes_tab, evidence_tab, quality_tab, intraday_tab = st.tabs(
        ["Overview", "Live setups", "Outcomes", "Evidence", "Model quality", "Intraday"]
    )

    with overview_tab:
        st.subheader("Research pipeline")
        st.caption("Candidates need supportive research and live confirmation before becoming a BUY setup.")
        if candidates:
            candidate_df = pd.DataFrame(candidates)
            candidate_cols = [
                "symbol", "verdict", "research_score", "news_score", "analyst_score",
                "fundamentals_score", "analyst_upside_pct", "catalyst", "source", "updated_at",
            ]
            candidate_df = candidate_df[[c for c in candidate_cols if c in candidate_df.columns]]
            candidate_df = candidate_df.rename(columns={
                "symbol": "Ticker", "verdict": "Research view", "research_score": "Score",
                "news_score": "News", "analyst_score": "Analysts", "fundamentals_score": "Fundamentals",
                "analyst_upside_pct": "Analyst upside %", "catalyst": "Catalyst",
                "source": "Discovery source", "updated_at": "Updated (UTC)",
            })
            verdicts = ["All views"] + sorted(candidate_df["Research view"].dropna().astype(str).unique().tolist()) if "Research view" in candidate_df else ["All views"]
            selected_verdict = st.selectbox("Research view", verdicts, key="equity_research_view")
            if selected_verdict != "All views" and "Research view" in candidate_df:
                candidate_df = candidate_df[candidate_df["Research view"] == selected_verdict]
            streamlit_ui.dataframe(candidate_df, use_container_width=True, hide_index=True)
        else:
            st.info("No fresh positive news or analyst catalysts are available. The list updates with the news feed.")

        st.subheader("How an equity setup is confirmed")
        flow_cols = streamlit_ui.columns(4)
        flow = [
            ("01 · Discover", "Company news and explicit analyst calls"),
            ("02 · Research", "Catalyst, fundamentals, valuation, and risk"),
            ("03 · Confirm", "Live price, volume, and market context"),
            ("04 · Track", "Entry, stop, target, and outcome evidence"),
        ]
        for col, (heading, detail) in zip(flow_cols, flow):
            with col:
                st.markdown(f"**{heading}**")
                st.caption(detail)

    with intraday_tab:
        st.subheader("Today's broker intraday ideas")
        st.caption(
            "Same-day public recommendation articles discovered through Google News RSS. "
            "Broker overlap counts only brokerages explicitly named in the article. "
            "These are reported calls, not confirmed live setups."
        )
        try:
            aliases = load_stock_aliases(
                settings.kite_instruments_csv, settings.equity_watchlist_path
            )
            intraday = collect_intraday_recommendations(
                aliases, list_news(limit=500), settings.market_timezone
            )
        except Exception as exc:
            intraday = {"recommendations": [], "common": [], "news_match_count": 0, "query_links": []}
            st.warning(f"Could not load intraday recommendation sources: {type(exc).__name__}: {exc}")

        recommendations = intraday["recommendations"]
        common = intraday["common"]
        live_prices = {
            str(row.get("symbol", "")).upper(): row
            for row in scanner_rows
        }
        display_recommendations = []
        for source_row in recommendations:
            row = dict(source_row)
            live_row = live_prices.get(str(row.get("symbol", "")).upper(), {})
            row["cmp_price"] = live_row.get("last_price") or row.get("cmp_price")
            row["price_as_of"] = live_row.get("last_tick_at")
            entry_low, entry_high = row.get("entry_low"), row.get("entry_high")
            row["entry_zone_display"] = (
                f"₹{entry_low:,.2f} to ₹{entry_high:,.2f}"
                if entry_low is not None and entry_high is not None and entry_low != entry_high
                else f"₹{entry_low:,.2f}" if entry_low is not None else "Not stated"
            )
            for source_key, display_key in (
                ("cmp_price", "cmp_display"), ("target_1", "target_1_display"),
                ("target_2", "target_2_display"), ("stop_loss", "stop_display"),
            ):
                value = row.get(source_key)
                row[display_key] = f"₹{float(value):,.2f}" if value is not None else "Not stated"
            row["exit_display"] = row.get("exit_by") or "Not stated"
            row["catalyst_display"] = row.get("catalyst") or row.get("title") or "Not stated"
            row["broker_display"] = row.get("broker") or "Not named"
            display_recommendations.append(row)
        news_matches = [row for row in display_recommendations if row["in_latest_news"]]
        display_columns = {
            "symbol": "Stock", "cmp_display": "CMP (live)", "entry_zone_display": "Entry Zone",
            "target_1_display": "Target 1", "target_2_display": "Target 2",
            "stop_display": "Stop-Loss", "exit_display": "Exit By",
            "catalyst_display": "Catalyst", "side": "View", "broker_display": "Broker",
            "price_as_of": "Price as of", "url": "Source",
        }
        visible_intraday_columns = list(display_columns)
        def render_intraday_table(rows: list[dict[str, Any]]) -> None:
            if not rows:
                return
            table = pd.DataFrame(rows)
            keep = [column for column in visible_intraday_columns if column in table.columns]
            table = table[keep].rename(columns=display_columns)
            streamlit_ui.dataframe(
                table, use_container_width=True, hide_index=True,
                column_config={"Source": streamlit_ui.column_config.LinkColumn(
                    "Source", display_text="Open"
                )},
            )
        kpis = streamlit_ui.columns(3)
        kpis[0].metric("Same-day articles matched", len(recommendations))
        kpis[1].metric("Stocks named by 2+ brokers", len(common))
        kpis[2].metric("Also in latest company news", len(news_matches))

        with st.expander("Common across explicitly named brokers", expanded=True):
            if common:
                common_df = pd.DataFrame(common).rename(columns={
                    "symbol": "Ticker", "brokers": "Named brokers",
                    "broker_count": "Broker count", "in_latest_news": "In latest news",
                    "latest_news_sources": "Latest news publishers",
                })
                streamlit_ui.dataframe(common_df, use_container_width=True, hide_index=True)
            else:
                st.info(
                    "No stock is currently named by at least two distinct brokerages in today's matched articles. "
                    "Unattributed media stories are excluded from broker consensus."
                )

        with st.expander("Broker recommendations also mentioned in latest news", expanded=True):
            if news_matches:
                render_intraday_table(news_matches)
            else:
                st.info("No matched same-day recommendation ticker also appears in today's company-news feed.")

        st.subheader("Today’s intraday stocks")
        if display_recommendations:
            render_intraday_table(display_recommendations)
        else:
            st.info(
                "No same-day articles matched the intraday recommendation terms and a known NSE company or ticker. "
                "This can also happen when Google News has not indexed today's calls yet."
            )
        st.caption(
            "Google News RSS is refreshed at most every 5 minutes. The article publisher is shown separately from "
            "the named brokerage; a media article is not counted as a broker call unless the broker is explicitly named. "
            "Results can be incomplete or delayed, and stock-name matching may miss aliases."
        )
        if intraday["query_links"]:
            st.markdown(
                "Check searches directly: " + " · ".join(
                    f"[Query {index + 1}]({url})"
                    for index, url in enumerate(intraday["query_links"])
                )
            )

    with setups_tab:
        st.subheader("Open BUY setups")
        st.caption("Use entry, stop, target, and research basis together. Holding-time estimates are extrapolations, not forecasts.")
        if open_rows:
            open_df = add_research_columns(pd.DataFrame(open_rows))
            cols = [
                "created_at", "symbol", "research_verdict", "research_score", "analyst_upside_pct",
                "total_score", "entry_price", "target_price", "stop_loss", "reward_risk",
                "expected_holding_minutes", "status", "research_summary", "reason",
            ]
            open_df = open_df[[c for c in cols if c in open_df.columns]].rename(columns={
                "created_at": "Created (UTC)", "symbol": "Ticker", "research_verdict": "Research",
                "research_score": "Research score", "analyst_upside_pct": "Analyst upside %",
                "total_score": "Setup score", "entry_price": "Entry", "target_price": "Target",
                "stop_loss": "Stop", "reward_risk": "Reward / risk",
                "expected_holding_minutes": "Est. hold (min)", "research_summary": "Research basis",
            })
            streamlit_ui.dataframe(open_df, use_container_width=True, hide_index=True)
        else:
            st.info("No open BUY setups right now. New candidates appear here after live confirmation.")
        with st.expander("Scanner coverage and rejections", expanded=False):
            if scanner_rows:
                scanner_df = pd.DataFrame(scanner_rows)
                cols = ["symbol", "last_price", "last_tick_at", "one_minute_bars", "five_minute_bars", "last_evaluated_at", "rejection_reason"]
                scanner_df = scanner_df[[c for c in cols if c in scanner_df.columns]].rename(columns={
                    "symbol": "Ticker", "last_price": "Latest price", "last_tick_at": "Last tick",
                    "one_minute_bars": "1m bars", "five_minute_bars": "5m bars",
                    "last_evaluated_at": "Last evaluation", "rejection_reason": "Scanner status / rejection",
                })
                streamlit_ui.dataframe(scanner_df, use_container_width=True, hide_index=True)
            else:
                st.info("No per-stock scanner diagnostics yet. Check worker logs for watchlist or subscription errors.")

    with outcomes_tab:
        st.subheader("Today's resolved setups")
        outcome_cols = ["resolved_at", "symbol", "entry_price", "stop_loss", "target_price", "result_price", "total_score"]
        left, right = streamlit_ui.columns(2)
        for col, title, rows, label in [
            (left, "Target reached", target_rows, "Hit bar close"),
            (right, "Stop reached", stop_rows, "Hit bar close"),
        ]:
            with col:
                st.markdown(f"**{title} · {len(rows)}**")
                if rows:
                    frame = pd.DataFrame(rows)
                    frame = frame[[c for c in outcome_cols if c in frame.columns]].rename(columns={
                        "resolved_at": "Resolved (UTC)", "symbol": "Ticker", "entry_price": "Entry",
                        "stop_loss": "Stop", "target_price": "Target", "result_price": label, "total_score": "Score",
                    })
                    streamlit_ui.dataframe(frame, use_container_width=True, hide_index=True)
                else:
                    st.caption("No outcomes in this category today.")
        st.markdown("**Expired setups**")
        if expired_rows:
            expired_df = pd.DataFrame(expired_rows)
            cols = ["resolved_at", "symbol", "entry_price", "stop_loss", "target_price", "expected_holding_minutes", "total_score"]
            expired_df = expired_df[[c for c in cols if c in expired_df.columns]].rename(columns={
                "resolved_at": "Expired (UTC)", "symbol": "Ticker", "entry_price": "Entry",
                "stop_loss": "Stop", "target_price": "Target", "expected_holding_minutes": "Est. hold (min)",
                "total_score": "Score",
            })
            streamlit_ui.dataframe(expired_df, use_container_width=True, hide_index=True)
        else:
            st.caption("No setups have expired today.")

        st.divider()
        st.subheader("Forward outcome quality")
        cost_bps = float(streamlit_ui.number_input(
            "Illustrative round-trip trading cost (bps)", min_value=0.0, max_value=1000.0,
            value=25.0, step=5.0, key="equity_round_trip_cost_bps",
            help="Change this to approximate your brokerage, taxes, and slippage. It is not a broker quote.",
        ))
        outcome = equity_signal_performance(cost_bps)
        metrics = [
            ("Open setups", outcome["open_signals"]),
            ("Resolved", outcome["resolved"]),
            ("Ambiguous", outcome["ambiguous"]),
            ("Expired", outcome["expired"]),
            ("Gross target-hit rate", f"{outcome['accuracy_percent']}%" if outcome["accuracy_percent"] is not None else "—"),
            ("Net profitable rate", f"{outcome['net_win_rate_percent']}%" if outcome["net_win_rate_percent"] is not None else "—"),
            ("Average net return", f"{outcome['net_average_return_percent']}%" if outcome["net_average_return_percent"] is not None else "—"),
            ("Net expectancy", f"{outcome['net_expectancy_r']} R" if outcome["net_expectancy_r"] is not None else "—"),
            ("Net profit factor", outcome["net_profit_factor"] if outcome["net_profit_factor"] is not None else "—"),
            ("Cost assumption", f"{cost_bps:.1f} bps"),
        ]
        for offset in range(0, len(metrics), 5):
            cols = streamlit_ui.columns(5)
            for col, (label, value) in zip(cols, metrics[offset:offset + 5]):
                col.metric(label, value)
        st.caption(
            "Forward scanner outcomes, not a historical backtest. Assumes fills at target or stop levels and subtracts "
            "the selected cost from entry notional; price gaps, liquidity, and broker-specific charges are not modeled. "
            "Target-hit rate excludes ambiguous, expired, and open setups."
        )

    with evidence_tab:
        st.subheader("Signal evidence trail")
        st.caption("Open a signal to inspect the checks and source articles captured when it was evaluated.")
        render_equity_evidence(list_equity_signals(limit=50))
        with st.expander("Optional candle pattern context", expanded=False):
            pattern_rows = list_recent_equity_patterns(100)
            if pattern_rows:
                pattern_df = pd.DataFrame(pattern_rows)
                cols = ["timestamp", "symbol", "timeframe", "pattern_names"]
                pattern_df = pattern_df[[c for c in cols if c in pattern_df.columns]].rename(columns={
                    "timestamp": "Candle time", "symbol": "Ticker", "timeframe": "Timeframe",
                    "pattern_names": "Detected patterns",
                })
                streamlit_ui.dataframe(pattern_df, use_container_width=True, hide_index=True)
            else:
                st.info("No candlestick patterns detected in stored equity candles yet.")
            st.caption("Patterns provide supporting context only. They never block or trigger an equity BUY.")

    with quality_tab:
        st.subheader("Research model learning status")
        report_path = Path("data/equity_live_learning_report.json")
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                holdout = report.get("holdout") or {}
                status_col, resolved_col, changed_col = streamlit_ui.columns(3)
                status_col.metric("Status", str(report.get("status", "UNKNOWN")))
                resolved_col.metric("Resolved samples", int(report.get("resolved_samples", 0)))
                changed_col.metric("Active model changed", "Yes" if report.get("active_model_changed") else "No")
                if holdout:
                    st.markdown("**Chronological holdout**")
                    hcols = streamlit_ui.columns(3)
                    hcols[0].metric("Samples", holdout.get("samples", 0))
                    hcols[1].metric("Win rate", f"{holdout.get('win_rate_pct', 'N/A')}%")
                    hcols[2].metric("Brier improvement vs baseline", holdout.get("brier_improvement_vs_baseline", "N/A"))
                    st.caption("Holdout results are reported for monitoring; learning does not silently change live signal thresholds.")
            except (OSError, ValueError, TypeError) as exc:
                st.warning(f"Could not read the equity learning report: {exc}")
        else:
            st.info("No learning report yet. Evaluation needs at least 100 resolved equity target/stop outcomes with feature snapshots.")
        st.subheader("Scanner coverage")
        if scanner_rows:
            st.metric("Stocks evaluated", len(scanner_rows))
            status_counts = pd.DataFrame(scanner_rows)
            if "rejection_reason" in status_counts:
                summary = status_counts["rejection_reason"].fillna("Eligible / no rejection").value_counts().rename_axis("Status").reset_index(name="Stocks")
                streamlit_ui.dataframe(summary, use_container_width=True, hide_index=True)
        else:
            st.info("No scanner diagnostics are available yet.")
        st.caption(f"Configured equity watchlist: {settings.equity_watchlist_path}")


@st.fragment(run_every=settings.news_refresh_seconds)
def render_news() -> None:
    st.subheader("Recent News")
    st.caption(f"Configured news feeds refresh every {settings.news_refresh_seconds} seconds. Analyst labels are extracted from explicit headline language and should be checked against the article.")
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
            "analyst_recommendation", "market_bias", "published_at", "url",
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
                "analyst_recommendation": streamlit_ui.column_config.TextColumn("Analyst view"),
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
