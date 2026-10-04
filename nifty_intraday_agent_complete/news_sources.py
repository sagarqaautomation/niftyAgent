import re
from datetime import datetime, timezone
import feedparser

SOURCES = {
    "Moneycontrol": "https://www.moneycontrol.com/rss/marketreports.xml",
    "LiveMint": "https://www.livemint.com/rss/markets"
}

BULLISH = {
    "rally","surge","gain","gains","higher","bullish","growth","strong",
    "upgrade","inflow","record high","beat","positive","recovery"
}
BEARISH = {
    "fall","falls","fell","drop","drops","lower","bearish","crash","loss",
    "downgrade","outflow","weak","negative","selloff","selling"
}

def classify(text):
    text = (text or "").lower()
    bull = sum(1 for x in BULLISH if x in text)
    bear = sum(1 for x in BEARISH if x in text)
    score = bull - bear
    if score >= 2:
        bias = "BULLISH"
    elif score <= -2:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"
    return score, bias

def fetch_feed(source, url, limit=30):
    feed = feedparser.parse(url)
    items = []
    for e in feed.entries[:limit]:
        title = e.get("title", "").strip()
        summary = re.sub("<[^>]+>", " ", e.get("summary", ""))
        score, bias = classify(title + " " + summary)
        published = e.get("published") or e.get("updated")
        items.append({
            "source": source,
            "title": title,
            "url": e.get("link"),
            "published_at": published,
            "sentiment": score,
            "market_bias": bias
        })
    return items

def fetch_all():
    items = []
    for source, url in SOURCES.items():
        try:
            items.extend(fetch_feed(source, url))
        except Exception as exc:
            print(f"News source failed: {source}: {exc}")
    return items

def aggregate_news(items):
    score = sum(i.get("sentiment", 0) for i in items)
    if score >= 3:
        return "BULLISH"
    if score <= -3:
        return "BEARISH"
    return "NEUTRAL"
