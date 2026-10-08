import calendar
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Literal

import feedparser as _feedparser  # type: ignore[reportMissingTypeStubs]
import requests

feedparser: Any = _feedparser

NewsItem = dict[str, Any]

SOURCES = {
    "Moneycontrol": "https://www.moneycontrol.com/rss/marketreports.xml",
    "LiveMint": "https://www.livemint.com/rss/markets",
    "Economic Times": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Business Standard": "https://www.business-standard.com/rss/markets-106.rss",
}

EVENT_TERMS: dict[str, tuple[str, ...]] = {
    "EARNINGS": (
        "earnings", "quarterly results", "q1 results", "q2 results", "q3 results",
        "q4 results", "net profit", "revenue growth", "results preview",
    ),
    "COMPANY_ANNOUNCEMENT": (
        "business update", "order win", "order book", "dividend", "buyback",
        "board meeting", "acquisition", "merger", "demerger", "block deal",
        "stake sale", "rights issue", "regulatory filing", "contract worth",
    ),
    "ANALYST_ACTION": (
        "target price", "price target", "buy rating", "sell rating", "hold rating",
        "rating upgrade", "rating downgrade", "upgrades", "downgrades",
        "brokerage", "analyst recommendation", "stock recommendation",
    ),
    "SECTOR": (
        "sector outlook", "sector stocks", "banking stocks", "auto stocks",
        "pharma stocks", "it stocks", "fmcg stocks", "metal stocks",
        "industry outlook", "sector index",
    ),
    "MARKET_MACRO": (
        "nifty", "sensex", "rbi", "repo rate", "rupee", "crude oil", "brent",
        "inflation", "treasury yield", "fii", "fpi", "foreign investors",
        "global markets", "market-wide", "stock market crash",
    ),
}

BULLISH = {
    "rally", "surge", "gain", "gains", "higher", "bullish", "growth", "strong",
    "upgrade", "inflow", "record high", "beat", "positive", "recovery",
    "beats estimates", "raises guidance", "raised guidance", "target raised",
    "buy rating", "order win", "order wins", "profit rises", "revenue growth",
}
BEARISH = {
    "fall", "falls", "fell", "drop", "drops", "lower", "bearish", "crash", "loss",
    "downgrade", "outflow", "weak", "negative", "selloff", "selling",
    "misses estimates", "cuts guidance", "cut guidance", "cuts target",
    "lowers target", "reduces target", "target cut",
    "sell rating", "order cancelled", "profit falls", "revenue declines",
    "regulatory penalty", "fraud investigation", "default notice",
}


def classify(text: str) -> tuple[int, Literal["BULLISH", "BEARISH", "NEUTRAL"]]:
    text = (text or "").lower()
    bull = sum(1 for x in BULLISH if x in text)
    bear = sum(1 for x in BEARISH if x in text)
    score = bull - bear
    if score >= 2:
        return score, "BULLISH"
    if score <= -2:
        return score, "BEARISH"
    return score, "NEUTRAL"


def _published_epoch(entry: Any) -> float | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return float(calendar.timegm(parsed))
        except (TypeError, ValueError, OverflowError):
            pass
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def classify_categories(text: str) -> list[str]:
    normalized = (text or "").lower()
    return [
        category
        for category, terms in EVENT_TERMS.items()
        if any(term in normalized for term in terms)
    ]


def _alias_pattern(alias: str) -> re.Pattern[str] | None:
    normalized = re.sub(r"\s+", " ", alias.strip())
    normalized = re.sub(r"\s+(?:limited|ltd\.?|private|pvt\.?|company)$", "", normalized, flags=re.IGNORECASE)
    if len(normalized) < 3:
        return None
    phrase = re.escape(normalized).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![A-Z0-9]){phrase}(?![A-Z0-9])", re.IGNORECASE)


def tag_news_symbols(
    items: list[NewsItem],
    aliases_by_symbol: Mapping[str, Sequence[str]],
) -> list[NewsItem]:
    for item in items:
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        matched: list[str] = []
        for symbol, aliases in aliases_by_symbol.items():
            candidates = dict.fromkeys([symbol, *aliases])
            if any(
                pattern.search(text)
                for alias in candidates
                if (pattern := _alias_pattern(str(alias))) is not None
            ):
                matched.append(symbol.upper())
        item["symbols"] = sorted(set(matched))
    return items


def fetch_feed(source: str, url: str, limit: int = 30) -> list[NewsItem]:
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; NiftyAgent/1.0)"},
        timeout=15,
    )
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Invalid RSS response from {source}")
    items: list[NewsItem] = []
    for entry in feed.entries[:limit]:
        title = entry.get("title", "").strip()
        summary = re.sub("<[^>]+>", " ", entry.get("summary", ""))
        score, bias = classify(title + " " + summary)
        published = entry.get("published") or entry.get("updated")
        categories = classify_categories(f"{title} {summary}")
        items.append({
            "source": source,
            "title": title,
            "summary": summary.strip(),
            "url": entry.get("link"),
            "published_at": published,
            "published_epoch": _published_epoch(entry),
            "sentiment": score,
            "market_bias": bias,
            "categories": categories,
            "symbols": [],
        })
    return items


def fetch_all(
    aliases_by_symbol: Mapping[str, Sequence[str]] | None = None,
) -> list[NewsItem]:
    items: list[NewsItem] = []
    for source, url in SOURCES.items():
        try:
            items.extend(fetch_feed(source, url))
        except Exception as exc:
            print(f"News source failed: {source}: {exc}")
    return tag_news_symbols(items, aliases_by_symbol or {})


def aggregate_news(
    items: list[NewsItem],
    max_age_minutes: int = 60,
    now: datetime | None = None,
) -> Literal["BULLISH", "BEARISH", "NEUTRAL"]:
    current_epoch = (now or datetime.now(timezone.utc)).timestamp()
    recent: list[NewsItem] = []
    max_age = max(1, max_age_minutes) * 60

    for item in items:
        epoch = item.get("published_epoch")
        if epoch is None:
            # Undated headlines should not influence an intraday decision.
            continue
        if 0 <= current_epoch - float(epoch) <= max_age:
            recent.append(item)

    unique: dict[str, NewsItem] = {}
    for item in recent:
        key = re.sub(r"\W+", " ", str(item.get("title", "")).lower()).strip()
        unique.setdefault(key, item)
    score = sum(
        float(item.get("sentiment", 0) or 0)
        for item in unique.values()
        if "MARKET_MACRO" in item.get("categories", [])
    )
    if score >= 3:
        return "BULLISH"
    if score <= -3:
        return "BEARISH"
    return "NEUTRAL"


def equity_news_bias(
    items: list[NewsItem],
    symbol: str,
    aliases: Sequence[str] = (),
    max_age_minutes: int = 60,
    now: datetime | None = None,
    availability_based: bool = False,
) -> Literal["BULLISH", "BEARISH", "NEUTRAL"]:
    current_epoch = (now or datetime.now(timezone.utc)).timestamp()
    max_age_seconds = max(1, max_age_minutes) * 60
    seen: set[str] = set()
    score = 0.0

    for item in items:
        timestamp_key = "available_epoch" if availability_based else "published_epoch"
        article_epoch = item.get(timestamp_key)
        if article_epoch is None:
            continue
        age = current_epoch - float(article_epoch)
        if age < 0 or age > max_age_seconds:
            continue

        title_key = re.sub(r"\W+", " ", str(item.get("title", "")).lower()).strip()
        if title_key in seen:
            continue
        seen.add(title_key)

        item_symbols = {str(value).upper() for value in item.get("symbols", [])}
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        alias_match = any(
            pattern.search(text)
            for alias in (symbol, *aliases)
            if (pattern := _alias_pattern(str(alias))) is not None
        )
        company_relevant = symbol.upper() in item_symbols or alias_match
        market_relevant = "MARKET_MACRO" in item.get("categories", [])
        if not company_relevant and not market_relevant:
            continue

        weight = 1.0 if company_relevant else 0.5
        score += float(item.get("sentiment", 0) or 0) * weight

    if score >= 2:
        return "BULLISH"
    if score <= -2:
        return "BEARISH"
    return "NEUTRAL"
