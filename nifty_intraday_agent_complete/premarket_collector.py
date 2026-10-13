"""Live pre-market context collection using available quote/news sources.

GIFT Nifty requires a provider-supported Kite quote key. Global indices use a
best-effort public Yahoo Finance chart endpoint. Missing data remain missing.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import requests

from config import settings
from market_context import load_constituent_weights, summarize_constituent_returns
from premarket_engine import build_premarket_context

YAHOO_SYMBOLS = {
    "^GSPC": "us_sp500",
    "^IXIC": "us_nasdaq",
    "^DJI": "us_dow",
    "^N225": "japan_nikkei",
    "^HSI": "hong_kong_hang_seng",
}


def _yahoo_change_pct(symbol: str) -> float | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    response = requests.get(
        url, params={"range": "5d", "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0 NiftyAgent/2.1"}, timeout=8,
    )
    response.raise_for_status()
    result = response.json().get("chart", {}).get("result") or []
    if not result:
        return None
    quote = result[0].get("indicators", {}).get("quote", [{}])[0]
    closes = [float(v) for v in (quote.get("close") or []) if v is not None]
    if len(closes) < 2 or closes[-2] == 0:
        return None
    return (closes[-1] / closes[-2] - 1.0) * 100.0


def _global_sentiment() -> tuple[float | None, dict[str, float]]:
    changes: dict[str, float] = {}
    for symbol, name in YAHOO_SYMBOLS.items():
        try:
            value = _yahoo_change_pct(symbol)
            if value is not None:
                changes[name] = value
        except Exception:
            continue
    if not changes:
        return None, {}
    return max(-1.0, min(1.0, (sum(changes.values()) / len(changes)) / 1.5)), changes


def collect_live_premarket_context(
    broker: Any,
    *,
    news_items: list[Mapping[str, Any]] | None = None,
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Collect best-effort live inputs; caller persists the immutable snapshot."""
    market_now = datetime.now(ZoneInfo(settings.market_timezone))
    observed_at = market_now.isoformat()
    quote_keys = ["NSE:INDIA VIX"]
    gift_key = settings.premarket_gift_nifty_quote
    if gift_key:
        quote_keys.append(gift_key)

    quote_errors: list[str] = []
    try:
        quotes = broker.quotes(quote_keys)
    except Exception as exc:
        quotes = {}
        quote_errors.append(f"kite_quotes:{type(exc).__name__}")

    vix_quote = quotes.get("NSE:INDIA VIX", {})
    vix_last = vix_quote.get("last_price")
    vix_close = (vix_quote.get("ohlc") or {}).get("close")
    vix_change_pct = (
        (float(vix_last) / float(vix_close) - 1.0) * 100.0
        if vix_last is not None and vix_close not in (None, 0) else None
    )
    gift_quote = quotes.get(gift_key, {}) if gift_key else {}
    gift_last = gift_quote.get("last_price")
    gift_close = (gift_quote.get("ohlc") or {}).get("close")
    gift_change_pct = (
        (float(gift_last) / float(gift_close) - 1.0) * 100.0
        if gift_last is not None and gift_close not in (None, 0) else None
    )

    global_value, global_changes = _global_sentiment()
    effective_weights = dict(weights or load_constituent_weights(settings.nifty50_weights_path))
    breadth_metrics: dict[str, Any] = {
        "bias": "NEUTRAL", "constituents_seen": 0, "availability": "unavailable"
    }
    breadth_ratio = None
    # Before 09:15, Kite last prices commonly reflect the prior close. Do not
    # mislabel those as live breadth or let them influence the pre-market score.
    if market_now.time().hour < 9 or (
        market_now.time().hour == 9 and market_now.time().minute < 15
    ):
        breadth_metrics["availability"] = "not_available_before_09:15_IST"
    elif effective_weights:
        instruments = [
            f"NSE:{symbol}" for symbol in effective_weights
            if symbol and symbol.replace("&", "").replace("-", "").replace(".", "").isalnum()
        ]
        try:
            constituent_quotes = broker.quotes(instruments)
            returns: dict[str, float] = {}
            for key, quote in constituent_quotes.items():
                symbol = key.split(":", 1)[-1].upper()
                last = quote.get("last_price")
                previous_close = (quote.get("ohlc") or {}).get("close")
                if last is not None and previous_close not in (None, 0):
                    returns[symbol] = (float(last) / float(previous_close) - 1.0) * 100.0
            breadth_metrics = summarize_constituent_returns(returns, effective_weights)
            advances = int(breadth_metrics.get("advancers", 0))
            declines = int(breadth_metrics.get("decliners", 0))
            if declines > 0:
                breadth_ratio = advances / declines
            elif advances > 0:
                breadth_ratio = 4.0
        except Exception as exc:
            quote_errors.append(f"breadth:{type(exc).__name__}")

    news_scores: list[float] = []
    now_epoch = time.time()
    for item in news_items or []:
        try:
            published = item.get("published_epoch")
            score = float(item.get("score", 0) or 0)
            if published is None:
                continue
            age = (now_epoch - float(published)) / 60.0
        except (TypeError, ValueError):
            continue
        if 0 <= age <= settings.premarket_news_max_age_minutes:
            news_scores.append(max(-1.0, min(1.0, score / 3.0)))
    news_sentiment = sum(news_scores) / len(news_scores) if news_scores else None

    context = build_premarket_context(
        gift_nifty_change_pct=gift_change_pct,
        global_sentiment=global_value,
        advance_decline_ratio=breadth_ratio,
        news_sentiment=news_sentiment,
        india_vix=vix_last,
        india_vix_change_pct=vix_change_pct,
        observed_at=observed_at,
    )
    context["data_sources"] = {
        "gift_nifty": "kite_quote" if gift_change_pct is not None else "unavailable",
        "india_vix": "kite_quote" if vix_last is not None else "unavailable",
        "global_indices_daily_change_pct": global_changes,
        "market_breadth": breadth_metrics,
        "news_headlines_used": len(news_scores),
        "quote_errors": quote_errors,
    }
    return context
