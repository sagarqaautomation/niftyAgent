"""Scheduled, signal-only pre-market report for GitHub Actions.

This is a short-lived report job, not a persistent live-tick worker. It never
places orders. Missing or stale provider data is reported as unavailable.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from config import settings
from news_sources import fetch_all
from premarket_collector import collect_live_premarket_context

TZ = ZoneInfo("Asia/Kolkata")
NSE_STATUS_URL = "https://www.nseindia.com/api/marketStatus"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 NiftyAgent/2.1",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/",
}


class UnavailableBroker:
    """No-op quote provider used when Kite credentials are not configured."""

    def quotes(self, instruments: list[str]) -> dict[str, Any]:
        return {}


def make_broker() -> Any:
    if not settings.kite_api_key or not settings.kite_access_token:
        print("Kite credentials unavailable; broker-only readings will be marked unavailable.")
        return UnavailableBroker()
    try:
        from kiteconnect import KiteConnect

        broker = KiteConnect(api_key=settings.kite_api_key)
        broker.set_access_token(settings.kite_access_token)
        return broker
    except Exception as exc:
        print(f"Kite client initialization failed ({type(exc).__name__}); continuing without broker quotes.")
        return UnavailableBroker()


def fetch_official_market_status() -> dict[str, Any]:
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get("https://www.nseindia.com/", timeout=8)
        response = session.get(NSE_STATUS_URL, timeout=8)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        print(f"Official NSE session status unavailable ({type(exc).__name__}).")
        return {}


def status_lines(payload: dict[str, Any]) -> list[str]:
    states = payload.get("marketState")
    if not isinstance(states, list):
        return ["- Official NSE market status: unavailable; holiday status not confirmed."]
    lines: list[str] = []
    for item in states:
        if not isinstance(item, dict):
            continue
        market = item.get("market") or item.get("marketType") or "Market"
        state = item.get("marketStatus") or item.get("status") or "unknown"
        trade_date = item.get("tradeDate")
        detail = f"- NSE {market}: **{state}**"
        if trade_date:
            detail += f" (trade date: {trade_date})"
        lines.append(detail)
    return lines or ["- Official NSE market status unavailable; holiday status not confirmed."]


def fmt(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "Unavailable"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def build_report(stage: str) -> tuple[str, dict[str, Any]]:
    now = datetime.now(TZ)
    status_payload = fetch_official_market_status()
    if now.weekday() >= 5:
        report = (
            f"# NiftyAgent {stage} report\n\n"
            f"- Generated: {now.isoformat()}\n"
            "- **Market session: weekend; no regular NSE equity session expected.**\n"
            "- No live market values are inferred and no orders are placed.\n"
        )
        return report, {"generated_at": now.isoformat(), "stage": stage, "market_session": "WEEKEND"}

    try:
        news_items = fetch_all()
    except Exception as exc:
        print(f"News collection unavailable ({type(exc).__name__}).")
        news_items = []

    context = collect_live_premarket_context(make_broker(), news_items=news_items)
    indicators = context.get("market_indicators", {})
    indicators = indicators if isinstance(indicators, dict) else {}
    investing = indicators.get("investing_com_india_50_futures", {}) or {}
    gift = indicators.get("gift_nifty", {}) or {}
    vix = indicators.get("india_vix", {}) or {}
    breadth = indicators.get("advance_decline", {}) or {}
    globals_data = context.get("data_sources", {}).get("global_indices_daily_change_pct", {})
    globals_data = globals_data if isinstance(globals_data, dict) else {}

    lines = [
        f"# NiftyAgent {stage} report",
        "",
        f"- Generated at: **{now.isoformat()}**",
        f"- Stage: **{stage}** (scheduled runs can be delayed by GitHub Actions)",
        "- Official NSE session status:",
        *status_lines(status_payload),
        "- Market holiday: **not confirmed** unless the official status explicitly says holiday.",
        "",
        "## Market indicators",
        "",
        "| Indicator | Reading | Source / freshness |",
        "|---|---|---|",
        f"| Investing.com India 50 futures | {fmt(investing.get('price'))}; {fmt(investing.get('change_pct'), '%')}; {investing.get('direction', 'UNAVAILABLE')} | {investing.get('url', 'Investing.com')}; fetched {investing.get('fetched_at', 'timestamp unavailable')} |",
        f"| GIFT Nifty | {fmt(gift.get('price'))}; {fmt(gift.get('change_pct'), '%')}; {gift.get('direction', 'UNAVAILABLE')} | {gift.get('source', 'source unavailable')}; source time {gift.get('timestamp') or 'unavailable'} |",
        f"| India VIX | {fmt(vix.get('value'))}; change {fmt(vix.get('change_pct'), '%')} | {vix.get('source', 'unavailable')}; threshold 15; live quote not guaranteed |",
        f"| NSE advances / declines | {fmt(breadth.get('advances'))} / {fmt(breadth.get('declines'))} | {breadth.get('source', 'unavailable')}; source time {breadth.get('timestamp') or 'unavailable'}; status {breadth.get('availability', 'unavailable')} |",
        f"| Advance/decline ratio | {fmt(breadth.get('advance_decline_ratio', context.get('advance_decline_ratio')))} | only valid when source counts are current and reliable |",
        "",
        "## Global markets",
        "",
    ]
    if globals_data:
        lines.extend(f"- {name}: {change:+.2f}%" for name, change in globals_data.items())
    else:
        lines.append("- Global index changes unavailable.")
    lines.extend([
        "",
        "## Context summary",
        "",
        f"- Directional bias: **{context.get('directional_bias', 'INSUFFICIENT_DATA')}**",
        f"- Directional score: {fmt(context.get('directional_score'))} (heuristic research score, not a profit probability)",
        f"- Volatility regime: **{context.get('volatility_regime', 'UNKNOWN')}**",
        f"- Risk flags: {', '.join(context.get('risk_flags', [])) or 'none reported'}",
        "- Suggested action: **WAIT for confirmation from the existing signal engine**; this report does not issue a CALL/PUT instruction.",
        "- AUTO_ORDER_PLACEMENT: **false**; this job is analysis-only.",
        "",
        "## Recent market headlines",
        "",
    ])
    relevant_news = sorted(
        [item for item in news_items if isinstance(item, dict)],
        key=lambda item: float(item.get("published_epoch") or 0),
        reverse=True,
    )[:8]
    if relevant_news:
        for item in relevant_news:
            title = str(item.get("title") or "Untitled headline").replace("[", "(").replace("]", ")")
            source = str(item.get("source") or "News source")
            url = str(item.get("url") or "")
            published = item.get("published_at") or "publication time unavailable"
            if url.startswith("https://"):
                lines.append(f"- [{title}]({url}) — {source}; {published}")
            else:
                lines.append(f"- {title} — {source}; {published}")
    else:
        lines.append("- Headlines unavailable.")
    lines.extend([
        "",
        "## Sources and limitations",
        "",
        "- [NSE India](https://www.nseindia.com/)",
        "- [Investing.com India 50 futures](https://www.investing.com/indices/india-50-futures)",
        "- [India VIX](https://www.nseindia.com/reports-indices-historical-vix)",
        "- Missing, blocked, stale or unverified fields are shown as unavailable; they are not guessed.",
        "- A scheduled GitHub Actions job is not a continuous tick feed and may start late.",
        "",
    ])
    data = {
        "generated_at": now.isoformat(),
        "stage": stage,
        "market_status": status_payload,
        "context": context,
        "news": relevant_news,
        "auto_order_placement": False,
    }
    return "\n".join(lines), data


def main() -> int:
    stage = sys.argv[1] if len(sys.argv) > 1 else "premarket"
    if stage not in {"premarket", "opening-confirmation"}:
        print("Usage: python scheduled_premarket_report.py [premarket|opening-confirmation]", file=sys.stderr)
        return 2

    report, payload = build_report(stage)
    output_dir = Path("artifacts")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    markdown_path = output_dir / f"niftyagent-{stage}-{stamp}.md"
    json_path = output_dir / f"niftyagent-{stage}-{stamp}.json"
    markdown_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(report)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(report)
    print(f"Report files: {markdown_path}, {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
