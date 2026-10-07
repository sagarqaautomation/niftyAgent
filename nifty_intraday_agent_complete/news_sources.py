import re
import time
from datetime import datetime, timezone
from typing import Any, Literal

import feedparser

NewsItem = dict[str, Any]

SOURCES = {
    "Moneycontrol": "https://www.moneycontrol.com/rss/marketreports.xml",
    "LiveMint": "https://www.livemint.com/rss/markets",
}

BULLISH = {
    "rally", "surge", "gain", "gains", "higher", "bullish", "growth", "strong",
    "upgrade", "inflow", "record high", "beat", "positive", "recovery",
}
BEARISH = {
    "fall", "falls", "fell", "drop", "drops", "lower", "bearish", "crash", "loss",
    "downgrade", "outflow", "weak", "negative", "selloff", "selling",
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
            return float(time.mktime(parsed))
        except (TypeError, ValueError, OverflowError):
            pass
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fetch_feed(source: str, url: str, limit: int = 30) -> list[NewsItem]:
    feed = feedparser.parse(url)
    items: list[NewsItem] = []
    for entry in feed.entries[:limit]:
        title = entry.get("title", "").strip()
        summary = re.sub("<[^>]+>", " ", entry.get("summary", ""))
        score, bias = classify(title + " " + summary)
        published = entry.get("published") or entry.get("updated")
        items.append({
            "source": source,
            "title": title,
            "url": entry.get("link"),
            "published_at": published,
            "published_epoch": _published_epoch(entry),
            "sentiment": score,
            "market_bias": bias,
        })
    return items


def fetch_all() -> list[NewsItem]:
    items: list[NewsItem] = []
    for source, url in SOURCES.items():
        try:
            items.extend(fetch_feed(source, url))
        except Exception as exc:
            print(f"News source failed: {source}: {exc}")
    return items


def aggregate_news(
    items: list[NewsItem],
    max_age_minutes: int = 60,
) -> Literal["BULLISH", "BEARISH", "NEUTRAL"]:
    now = datetime.now(timezone.utc).timestamp()
    recent: list[NewsItem] = []
    max_age = max(1, max_age_minutes) * 60

    for item in items:
        epoch = item.get("published_epoch")
        if epoch is None:
            # Undated headlines should not influence an intraday decision.
            continue
        if 0 <= now - float(epoch) <= max_age:
            recent.append(item)

    score = sum(float(item.get("sentiment", 0) or 0) for item in recent)
    if score >= 3:
        return "BULLISH"
    if score <= -3:
        return "BEARISH"
    return "NEUTRAL"
