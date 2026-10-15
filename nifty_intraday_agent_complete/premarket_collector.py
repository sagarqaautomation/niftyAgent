"""Best-effort pre-market collection from public and broker quote sources.

Investing.com and NSE India public endpoints may block automated requests.
Missing readings remain unavailable rather than being inferred.
"""
from __future__ import annotations

import time
from datetime import datetime
from html import unescape
import re
from typing import Any, Mapping, cast
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
INVESTING_INDIA_50_URL = "https://www.investing.com/indices/india-50-futures"
NSE_HOME_URL = "https://www.nseindia.com/"
NSE_MARKET_STATUS_URL = "https://www.nseindia.com/api/marketStatus"
NSE_ADVANCE_DECLINE_URL = "https://www.nseindia.com/api/live-analysis-advance?index=all"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 NiftyAgent/2.1",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/",
}


def _float_value(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _as_dict(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _market_direction(change_pct: float | None) -> str:
    if change_pct is None:
        return "UNAVAILABLE"
    if change_pct > 0:
        return "GREEN"
    if change_pct < 0:
        return "RED"
    return "FLAT"


def _investing_data_test_text(document: str, test_id: str) -> str | None:
    match = re.search(
        rf'<[^>]*data-test=["\']{re.escape(test_id)}["\'][^>]*>(.*?)</[^>]+>',
        document,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    text = re.sub(r"<[^>]+>", "", match.group(1))
    return unescape(text).strip()


def _parse_display_number(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.search(r"[-+]?\s*\d[\d,]*(?:\.\d+)?", value)
    if not match:
        return None
    return _float_value(match.group(0).replace(",", "").replace(" ", ""))


def _parse_display_percent(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.search(r"([-+]?\s*\d[\d,]*(?:\.\d+)?)\s*%", value)
    if not match:
        return None
    return _float_value(match.group(1).replace(",", "").replace(" ", ""))


def parse_investing_india_50(document: str, fetched_at: str) -> dict[str, Any]:
    price = _parse_display_number(
        _investing_data_test_text(document, "instrument-price-last")
    )
    change_pct = _parse_display_percent(
        _investing_data_test_text(document, "instrument-price-change-percent")
    )
    return {
        "price": price,
        "change_pct": change_pct,
        "direction": _market_direction(change_pct),
        "url": INVESTING_INDIA_50_URL,
        "fetched_at": fetched_at,
    }


def parse_nse_market_data(
    market_status: Mapping[str, Any],
    advance_decline: Mapping[str, Any],
    *,
    observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any], float | None]:
    raw_gift_data: Any = market_status.get("giftnifty")
    gift_data = _as_dict(raw_gift_data)
    gift_change = _float_value(gift_data.get("PERCHANGE"))
    gift: dict[str, Any] = {
        "price": _float_value(gift_data.get("LASTPRICE")),
        "change_pct": gift_change,
        "direction": _market_direction(gift_change),
        "timestamp": gift_data.get("TIMESTMP"),
    }

    raw_advance_data: Any = advance_decline.get("advance")
    advance_data = _as_dict(raw_advance_data)
    raw_count_data: Any = advance_data.get("count")
    count_data = _as_dict(raw_count_data)
    advances = _float_value(count_data.get("Advances"))
    declines = _float_value(count_data.get("Declines"))
    unchanged = _float_value(count_data.get("Unchanged", count_data.get("Unchang")))
    total = _float_value(count_data.get("Total"))
    if advances is None or declines is None:
        return gift, {
            "availability": "unavailable",
            "bias": "NEUTRAL",
            "advances": None,
            "declines": None,
            "unchanged": unchanged,
            "total": total,
            "timestamp": advance_decline.get("timestamp"),
        }, None

    advances = int(advances)
    declines = int(declines)
    breadth_timestamp = advance_decline.get("timestamp")
    observed = datetime.fromisoformat(observed_at)
    pre_open = observed.time() < datetime.strptime("09:15", "%H:%M").time()
    source_date = None
    if breadth_timestamp:
        try:
            source_date = datetime.strptime(
                str(breadth_timestamp), "%d-%b-%Y %H:%M:%S"
            ).date()
        except ValueError:
            pass
    is_current_session = source_date == observed.date() if source_date else None
    if advances > declines:
        breadth_bias = "BULLISH"
    elif declines > advances:
        breadth_bias = "BEARISH"
    else:
        breadth_bias = "NEUTRAL"
    breadth: dict[str, Any] = {
        "availability": (
            "available" if is_current_session is True else "latest_published_session"
        ),
        "bias": breadth_bias,
        "advances": advances,
        "declines": declines,
        "unchanged": int(unchanged) if unchanged is not None else None,
        "total": int(total) if total is not None else advances + declines,
        "timestamp": breadth_timestamp,
        "is_current_session": is_current_session,
    }
    if not pre_open and is_current_session is not True:
        ratio = None
    elif declines > 0:
        ratio = advances / declines
    elif advances > 0:
        ratio = 4.0
    else:
        ratio = None
    return gift, breadth, ratio


def _fetch_nse_market_data() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    errors: list[str] = []
    try:
        session.get(NSE_HOME_URL, timeout=8)
    except Exception as exc:
        errors.append(f"nse_cookie_bootstrap:{type(exc).__name__}")

    payloads: list[dict[str, Any]] = []
    for label, url in (
        ("nse_market_status", NSE_MARKET_STATUS_URL),
        ("nse_breadth", NSE_ADVANCE_DECLINE_URL),
    ):
        try:
            response = session.get(url, timeout=8)
            response.raise_for_status()
            payload: Any = response.json()
            payloads.append(_as_dict(payload))
        except Exception as exc:
            errors.append(f"{label}:{type(exc).__name__}")
            payloads.append({})
    return payloads[0], payloads[1], errors


def _yahoo_change_pct(symbol: str) -> float | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    response = requests.get(
        url, params={"range": "5d", "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0 NiftyAgent/2.1"}, timeout=8,
    )
    response.raise_for_status()
    payload: Any = response.json()
    payload_dict = _as_dict(payload)
    chart = _as_dict(payload_dict.get("chart"))
    results: Any = chart.get("result")
    if not isinstance(results, list) or not results:
        return None
    result = _as_dict(results[0])
    indicators = _as_dict(result.get("indicators"))
    quote_list: Any = indicators.get("quote")
    if not isinstance(quote_list, list) or not quote_list:
        return None
    quote = _as_dict(quote_list[0])
    raw_closes: Any = quote.get("close")
    if not isinstance(raw_closes, list):
        return None
    close_values = cast(list[Any], raw_closes)
    closes = [
        close for value in close_values
        if (close := _float_value(value)) is not None
    ]
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
    investing_market: dict[str, Any] = {
        "price": None,
        "change_pct": None,
        "direction": "UNAVAILABLE",
        "url": INVESTING_INDIA_50_URL,
        "fetched_at": observed_at,
    }
    try:
        response = requests.get(
            INVESTING_INDIA_50_URL,
            headers={"User-Agent": NSE_HEADERS["User-Agent"]},
            timeout=8,
        )
        response.raise_for_status()
        investing_market = parse_investing_india_50(response.text, observed_at)
    except Exception as exc:
        quote_errors = [f"investing_com:{type(exc).__name__}"]
    else:
        quote_errors = []

    nse_status, nse_advance_decline, nse_errors = _fetch_nse_market_data()
    nse_gift, nse_breadth, nse_breadth_ratio = parse_nse_market_data(
        nse_status, nse_advance_decline, observed_at=observed_at
    )
    quote_errors.extend(nse_errors)

    quote_keys = ["NSE:INDIA VIX"]
    gift_key = settings.premarket_gift_nifty_quote
    if gift_key:
        quote_keys.append(gift_key)

    try:
        quotes = _as_dict(broker.quotes(quote_keys))
    except Exception as exc:
        quotes = {}
        quote_errors.append(f"kite_quotes:{type(exc).__name__}")

    vix_quote = _as_dict(quotes.get("NSE:INDIA VIX"))
    vix_last = _float_value(vix_quote.get("last_price"))
    vix_ohlc = _as_dict(vix_quote.get("ohlc"))
    vix_close = _float_value(vix_ohlc.get("close"))
    vix_change_pct = (
        (vix_last / vix_close - 1.0) * 100.0
        if vix_last is not None and vix_close not in (None, 0) else None
    )
    gift_quote = _as_dict(quotes.get(gift_key)) if gift_key else {}
    gift_last = _float_value(gift_quote.get("last_price"))
    gift_ohlc = _as_dict(gift_quote.get("ohlc"))
    gift_close = _float_value(gift_ohlc.get("close"))
    kite_gift_change_pct = (
        (gift_last / gift_close - 1.0) * 100.0
        if gift_last is not None and gift_close not in (None, 0) else None
    )
    gift_change_pct = nse_gift["change_pct"]
    gift_source = "nse_market_status" if gift_change_pct is not None else "unavailable"
    if gift_change_pct is None and kite_gift_change_pct is not None:
        gift_change_pct = kite_gift_change_pct
        gift_source = "kite_quote"

    global_value, global_changes = _global_sentiment()
    effective_weights = dict(weights or load_constituent_weights(settings.nifty50_weights_path))
    breadth_metrics: dict[str, Any] = {
        "bias": "NEUTRAL", "constituents_seen": 0, "availability": "unavailable"
    }
    breadth_ratio = None
    pre_open = market_now.time().hour < 9 or (
        market_now.time().hour == 9 and market_now.time().minute < 15
    )
    if nse_breadth_ratio is not None and (
        pre_open or nse_breadth.get("availability") == "available"
    ):
        breadth_metrics = nse_breadth
        breadth_metrics["source"] = "nse_india"
        breadth_ratio = nse_breadth_ratio
    elif pre_open:
        breadth_metrics["availability"] = (
            "latest_published_session_unavailable"
            if nse_breadth.get("availability") == "latest_published_session"
            else "not_available_before_09:15_IST"
        )
    elif effective_weights:
        instruments = [
            f"NSE:{symbol}" for symbol in effective_weights
            if symbol and symbol.replace("&", "").replace("-", "").replace(".", "").isalnum()
        ]
        try:
            constituent_quotes = _as_dict(broker.quotes(instruments))
            returns: dict[str, float] = {}
            for key, raw_quote in constituent_quotes.items():
                quote = _as_dict(raw_quote)
                symbol = key.split(":", 1)[-1].upper()
                last = _float_value(quote.get("last_price"))
                ohlc = _as_dict(quote.get("ohlc"))
                previous_close = _float_value(ohlc.get("close"))
                if last is not None and previous_close not in (None, 0):
                    returns[symbol] = (last / previous_close - 1.0) * 100.0
            breadth_metrics = summarize_constituent_returns(returns, effective_weights)
            breadth_metrics["availability"] = "available"
            breadth_metrics["source"] = "kite_constituents"
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
        "gift_nifty": gift_source,
        "india_vix": "kite_quote" if vix_last is not None else "unavailable",
        "global_indices_daily_change_pct": global_changes,
        "market_breadth": breadth_metrics,
        "news_headlines_used": len(news_scores),
        "quote_errors": quote_errors,
    }
    context["market_indicators"] = {
        "investing_com_india_50_futures": investing_market,
        "gift_nifty": {
            **nse_gift,
            "change_pct": gift_change_pct,
            "source": gift_source,
        },
        "india_vix": {
            "value": vix_last,
            "change_pct": vix_change_pct,
            "source": "kite_quote" if vix_last is not None else "unavailable",
        },
        "advance_decline": breadth_metrics,
    }
    return context
