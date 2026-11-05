"""Collect same-day public intraday recommendation articles from Google News RSS.

Only explicitly named brokerages count toward broker consensus. Search-result
articles are evidence links, not broker-authored calls unless their text says so.
"""
from __future__ import annotations

import calendar
import csv
import re
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests

MARKET_TZ = ZoneInfo("Asia/Kolkata")
SEARCH_QUERIES = (
    '"intraday stocks to buy today" India',
    '"intraday stocks to sell today" India',
    '"intraday stock picks" broker India today',
)
BROKER_ALIASES = {
    "Motilal Oswal": ("Motilal Oswal", "MOFSL"),
    "ICICI Securities": ("ICICI Securities", "ICICI Direct"),
    "HDFC Securities": ("HDFC Securities",),
    "Angel One": ("Angel One",),
    "Kotak Securities": ("Kotak Securities",),
    "Sharekhan": ("Sharekhan",),
    "Axis Securities": ("Axis Securities",),
    "SBI Securities": ("SBI Securities",),
    "JM Financial": ("JM Financial",),
    "Nuvama": ("Nuvama",),
    "LKP Securities": ("LKP Securities",),
    "Geojit": ("Geojit",),
    "Samco Securities": ("Samco Securities",),
    "Aditya Trading Solutions": ("Aditya Trading Solutions", "ATS Share Brokers"),
    "IIFL Securities": ("IIFL Securities",),
    "Nirmal Bang": ("Nirmal Bang",),
}
_RECOMMENDATION_TERMS = re.compile(
    r"\bintraday\b|\bday[- ]trade\w*\b|\bstocks? to (?:buy|sell) today\b|"
    r"\bintraday (?:stock )?(?:calls?|picks?|recommendations?)\b",
    re.IGNORECASE,
)
_BUY = re.compile(r"\b(?:buy|bullish|long)\b", re.IGNORECASE)
_SELL = re.compile(r"\b(?:sell|bearish|short)\b", re.IGNORECASE)
_cache_lock = threading.Lock()
_cached_at = 0.0
_cached_day = ""
_cached_items: list[dict[str, Any]] = []


def load_stock_aliases(instruments_csv: str, watchlist_csv: str) -> dict[str, list[str]]:
    """Map listed symbols to official instrument names for article matching."""
    aliases: dict[str, list[str]] = defaultdict(list)
    for path in (instruments_csv, watchlist_csv):
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as source:
                reader = csv.DictReader(source)
                if not reader.fieldnames:
                    continue
                fields = {field.strip().lower(): field for field in reader.fieldnames}
                symbol_field = fields.get("tradingsymbol") or fields.get("symbol")
                name_field = fields.get("name") or fields.get("company_name")
                exchange_field = fields.get("exchange")
                type_field = fields.get("instrument_type")
                if not symbol_field:
                    continue
                for row in reader:
                    symbol = str(row.get(symbol_field) or "").strip().upper()
                    if not symbol or (exchange_field and row.get(exchange_field) not in {"NSE", ""}):
                        continue
                    if type_field and row.get(type_field) not in {"EQ", ""}:
                        continue
                    name = str(row.get(name_field) or "").strip() if name_field else ""
                    for alias in (symbol, name):
                        if len(alias) >= 3 and alias.casefold() not in {
                            value.casefold() for value in aliases[symbol]
                        }:
                            aliases[symbol].append(alias)
        except (OSError, csv.Error):
            continue
    return dict(aliases)


def _source_items() -> list[dict[str, Any]]:
    """Fetch a small set of public Google News RSS searches, with a 5-minute cache."""
    global _cached_at, _cached_day, _cached_items
    today = datetime.now(MARKET_TZ).date().isoformat()
    now = time.monotonic()
    with _cache_lock:
        if _cached_day == today and now - _cached_at < 300:
            return [dict(item) for item in _cached_items]

    fetched: list[dict[str, Any]] = []
    for query in SEARCH_QUERIES:
        url = (
            "https://news.google.com/rss/search?q="
            + quote_plus(query)
            + "&hl=en-IN&gl=IN&ceid=IN:en"
        )
        try:
            response = requests.get(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; NiftyAgent/1.0)"},
                timeout=10,
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            for entry in feed.entries[:20]:
                title = str(entry.get("title") or "").strip()
                summary = re.sub(r"<[^>]+>", " ", str(entry.get("summary") or "")).strip()
                published = entry.get("published") or entry.get("updated")
                source = entry.get("source") or {}
                publisher = str(source.get("title") or "").strip()
                link = str(entry.get("link") or "").strip()
                epoch = entry.get("published_parsed") or entry.get("updated_parsed")
                published_epoch = (
                    calendar.timegm(epoch) if epoch else None
                )
                fetched.append({
                    "title": title, "summary": summary, "published_at": published,
                    "published_epoch": published_epoch, "publisher": publisher,
                    "url": link, "query": query,
                })
        except (requests.RequestException, ValueError, TypeError) as exc:
            print(f"[INTRADAY IDEAS] Google News query failed: {type(exc).__name__}: {exc}", flush=True)

    with _cache_lock:
        _cached_at = time.monotonic()
        _cached_day = today
        _cached_items = fetched
    return [dict(item) for item in fetched]


_PRICE = r"(?:₹|Rs\.?\s*)?([0-9][0-9,]*(?:\.[0-9]+)?)"
_ENTRY_RE = re.compile(
    r"\bentry(?:\s+zone)?\s*[:@-]?\s*" + _PRICE
    + r"(?:\s*(?:to|[-–])\s*(?:₹|Rs\.?\s*)?([0-9][0-9,]*(?:\.[0-9]+)?))?",
    re.IGNORECASE,
)
_TARGET1_RE = re.compile(r"\b(?:target\s*1|t1)\s*[:@-]?\s*" + _PRICE, re.IGNORECASE)
_TARGET2_RE = re.compile(r"\b(?:target\s*2|t2)\s*[:@-]?\s*" + _PRICE, re.IGNORECASE)
_TARGET_RE = re.compile(r"\b(?:target|tgt)\s*[:@-]?\s*" + _PRICE, re.IGNORECASE)
_STOP_RE = re.compile(r"\b(?:stop[- ]?loss|sl)\s*[:@-]?\s*" + _PRICE, re.IGNORECASE)
_EXIT_RE = re.compile(
    r"\b(?:exit|square[- ]?off|book)\s+by\s+(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)",
    re.IGNORECASE,
)


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _call_details(text: str, title: str, summary: str) -> dict[str, Any]:
    entry = _ENTRY_RE.search(text)
    target1 = _TARGET1_RE.search(text)
    target2 = _TARGET2_RE.search(text)
    targets = [_number(match.group(1)) for match in _TARGET_RE.finditer(text)]
    target1_value = _number(target1.group(1)) if target1 else (targets[0] if targets else None)
    target2_value = _number(target2.group(1)) if target2 else (
        targets[1] if len(targets) > 1 else None
    )
    stop = _STOP_RE.search(text)
    exit_time = _EXIT_RE.search(text)
    catalyst = re.sub(r"\s+", " ", summary or title).strip()
    return {
        "cmp_price": None,
        "entry_low": _number(entry.group(1)) if entry else None,
        "entry_high": _number(entry.group(2)) if entry and entry.group(2) else (
            _number(entry.group(1)) if entry else None
        ),
        "target_1": target1_value,
        "target_2": target2_value,
        "stop_loss": _number(stop.group(1)) if stop else None,
        "exit_by": exit_time.group(1).strip() if exit_time else None,
        "catalyst": catalyst[:240] if catalyst else "",
    }


def collect_intraday_recommendations(
    stock_aliases: dict[str, list[str]],
    latest_news: list[dict[str, Any]],
    market_timezone: str = "Asia/Kolkata",
) -> dict[str, Any]:
    """Return same-day article mentions, conservative broker overlap, and news matches."""
    tz = ZoneInfo(market_timezone)
    today = datetime.now(tz).date()
    news_symbols: set[str] = set()
    for item in latest_news:
        try:
            epoch = float(item.get("published_epoch") or 0)
            if epoch and datetime.fromtimestamp(epoch, tz).date() == today:
                news_symbols.update(str(symbol).upper() for symbol in item.get("symbols", []))
        except (TypeError, ValueError, OSError):
            continue

    symbol_patterns: list[tuple[str, re.Pattern[str]]] = []
    for symbol, names in stock_aliases.items():
        for alias in names:
            alias = alias.strip()
            if not alias:
                continue
            pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])", re.IGNORECASE)
            symbol_patterns.append((symbol.upper(), pattern))

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in _source_items():
        title = item["title"]
        body = f"{title} {item['summary']}"
        epoch = item.get("published_epoch")
        if not epoch:
            continue
        if datetime.fromtimestamp(float(epoch), tz).date() != today:
            continue
        query = str(item.get("query") or "").casefold()
        has_buy, has_sell = bool(_BUY.search(body)), bool(_SELL.search(body))
        if "stocks to buy today" in query or "stocks to buy today" in body:
            side = "BUY" if has_buy or not has_sell else "SELL"
        elif "stocks to sell today" in query or "stocks to sell today" in body:
            side = "SELL"
        else:
            side = "MIXED" if has_buy and has_sell else "BUY" if has_buy else "SELL" if has_sell else "UNSPECIFIED"
        if side != "BUY":
            continue
        matches = {symbol for symbol, pattern in symbol_patterns if pattern.search(body)}
        if not matches:
            continue
        brokerage = next((canonical for canonical, aliases in BROKER_ALIASES.items()
                          if any(alias.casefold() in body.casefold() for alias in aliases)), "")
        publisher = item.get("publisher") or urlparse(item.get("url", "")).hostname or "Google News result"
        for symbol in matches:
            key = (symbol, item.get("url", ""))
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "symbol": symbol, "side": side, "broker": brokerage or "Not explicitly named",
                "publisher": publisher, "title": title, "published_at": item.get("published_at"),
                "published_epoch": epoch, "url": item.get("url"),
                "in_latest_news": symbol in news_symbols,
                **_call_details(body, title, item.get("summary", "")),
            })

    broker_sets: dict[str, set[str]] = defaultdict(set)
    latest_sets: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["broker"] != "Not explicitly named":
            broker_sets[row["symbol"]].add(row["broker"])
        if row["in_latest_news"]:
            latest_sets[row["symbol"]].add(row["publisher"])
    common = [
        {"symbol": symbol, "brokers": ", ".join(sorted(brokers)), "broker_count": len(brokers),
         "in_latest_news": symbol in latest_sets, "latest_news_sources": ", ".join(sorted(latest_sets[symbol]))}
        for symbol, brokers in broker_sets.items() if len(brokers) >= 2
    ]
    common.sort(key=lambda row: (-row["broker_count"], row["symbol"]))
    rows.sort(key=lambda row: float(row.get("published_epoch") or 0), reverse=True)
    return {
        "recommendations": rows,
        "common": common,
        "news_match_count": sum(bool(row["in_latest_news"]) for row in rows),
        "query_links": [
            "https://news.google.com/search?q=" + quote_plus(query) + "&hl=en-IN&gl=IN&ceid=IN%3Aen"
            for query in SEARCH_QUERIES
        ],
    }
