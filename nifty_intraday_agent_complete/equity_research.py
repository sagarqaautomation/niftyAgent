"""Research layer for equity BUY signals: news catalysts, analyst views,
fundamentals and valuation. Candlestick patterns play no part here.

All data sources fail soft: missing data scores zero rather than raising.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Mapping, Sequence

from news_sources import NewsItem, _alias_pattern  # type: ignore[attr-defined]

COMPANY_CATEGORIES = {"EARNINGS", "COMPANY_ANNOUNCEMENT", "ANALYST_ACTION"}
MIN_RESEARCH_SCORE = 2
AVOID_SCORE = -2

_TARGET_RE = re.compile(
    r"(?:target(?:\s+price)?|price\s+target)[^0-9₹]{0,25}(?:rs\.?|inr|₹)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
    re.IGNORECASE,
)
_POSITIVE_RATING_RE = re.compile(r"\b(buy|outperform|overweight|accumulate|upgrade[sd]?)\b", re.IGNORECASE)
_NEGATIVE_RATING_RE = re.compile(r"\b(sell|underperform|underweight|reduce|downgrade[sd]?)\b", re.IGNORECASE)

_FUNDAMENTALS_TTL = 24 * 3600
_FAILURE_TTL = 3600
_fundamentals_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_cache_lock = threading.Lock()


def _relevant(item: Mapping[str, Any], symbol: str, aliases: Sequence[str]) -> bool:
    if symbol.upper() in {str(s).upper() for s in item.get("symbols", [])}:
        return True
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    return any(
        p.search(text) for a in (symbol, *aliases)
        if (p := _alias_pattern(str(a))) is not None
    )


def assess_news(
    items: Sequence[NewsItem], symbol: str, aliases: Sequence[str] = (),
    max_age_hours: float = 24, now: float | None = None,
) -> dict[str, Any]:
    """Company-specific catalysts (results, orders, announcements)."""
    current = now if now is not None else time.time()
    seen: set[str] = set()
    score = 0.0
    headlines: list[str] = []
    for item in items:
        epoch = item.get("published_epoch")
        if epoch is None or not 0 <= current - float(epoch) <= max_age_hours * 3600:
            continue
        if not COMPANY_CATEGORIES.intersection(item.get("categories", [])):
            continue
        if not _relevant(item, symbol, aliases):
            continue
        key = re.sub(r"\W+", " ", str(item.get("title", "")).lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        sentiment = float(item.get("sentiment", 0) or 0)
        score += sentiment
        if sentiment:
            headlines.append(str(item.get("title", "")))
    points = 2 if score >= 3 else 1 if score >= 1 else -2 if score <= -3 else -1 if score <= -1 else 0
    return {"score": points, "raw_sentiment": score, "headlines": headlines[:5]}


def assess_analysts(
    items: Sequence[NewsItem], symbol: str, aliases: Sequence[str], price: float,
    max_age_hours: float = 72, now: float | None = None,
) -> dict[str, Any]:
    """Extract broker targets and ratings from analyst-action headlines."""
    current = now if now is not None else time.time()
    targets: list[dict[str, Any]] = []
    positive = negative = 0
    for item in items:
        epoch = item.get("published_epoch")
        if epoch is None or not 0 <= current - float(epoch) <= max_age_hours * 3600:
            continue
        if "ANALYST_ACTION" not in item.get("categories", []):
            continue
        if not _relevant(item, symbol, aliases):
            continue
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        if _NEGATIVE_RATING_RE.search(text):
            negative += 1
        elif _POSITIVE_RATING_RE.search(text):
            positive += 1
        match = _TARGET_RE.search(text)
        if match and price > 0:
            target = float(match.group(1).replace(",", ""))
            # Reject targets that cannot belong to this stock (parse noise).
            if 0.5 * price <= target <= 3 * price:
                targets.append({
                    "source": item.get("source"),
                    "title": item.get("title"),
                    "target": target,
                    "upside_pct": round((target / price - 1) * 100, 2),
                })
    avg_upside = sum(t["upside_pct"] for t in targets) / len(targets) if targets else None
    score = 0
    if negative > positive or (avg_upside is not None and avg_upside < 0):
        score = -2
    elif positive:
        score = 1
        if avg_upside is not None and avg_upside >= 10:
            score = 2
    elif avg_upside is not None and avg_upside >= 10:
        score = 1
    return {
        "score": score, "positive_calls": positive, "negative_calls": negative,
        "targets": targets[:5], "avg_target_upside_pct": avg_upside,
    }


def _fetch_fundamentals(symbol: str) -> dict[str, Any] | None:
    try:
        import yfinance as yf  # optional dependency
        info = yf.Ticker(f"{symbol}.NS").info or {}
    except Exception:
        return None
    keys = (
        "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth", "profitMargins",
        "debtToEquity", "operatingCashflow", "freeCashflow", "trailingPE",
        "forwardPE", "pegRatio", "returnOnEquity", "sector", "industry",
    )
    data = {k: info.get(k) for k in keys}
    return data if any(v is not None for v in data.values()) else None


def get_fundamentals(symbol: str) -> dict[str, Any] | None:
    now = time.time()
    with _cache_lock:
        cached = _fundamentals_cache.get(symbol)
        if cached and cached[0] > now:
            return cached[1]
    data = _fetch_fundamentals(symbol)
    with _cache_lock:
        ttl = _FUNDAMENTALS_TTL if data else _FAILURE_TTL
        _fundamentals_cache[symbol] = (now + ttl, data)
    return data


def peer_median_pe(symbol: str, sector: str | None) -> float | None:
    """Median trailing PE of other already-loaded stocks in the same sector.
    Uses only cached data, so it improves as the watchlist gets researched."""
    if not sector:
        return None
    with _cache_lock:
        peers = [
            data.get("trailingPE")
            for other, (expiry, data) in _fundamentals_cache.items()
            if other != symbol and data and data.get("sector") == sector
            and expiry > time.time()
        ]
    values = sorted(float(v) for v in peers if isinstance(v, (int, float)) and v > 0)
    if len(values) < 2:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def assess_fundamentals(
    data: Mapping[str, Any] | None, peer_pe: float | None = None
) -> dict[str, Any]:
    """Growth, profitability, leverage, cash flow and valuation. Yahoo reports
    debtToEquity in percent. Peer comparison is approximated by absolute PE/PEG
    bands because no peer dataset is available."""
    if not data:
        return {"score": 0, "available": False, "notes": ["fundamentals unavailable"]}
    score = 0
    notes: list[str] = []

    def num(key: str) -> float | None:
        value = data.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    growth = num("revenueGrowth")
    earnings = num("earningsGrowth") if num("earningsGrowth") is not None else num("earningsQuarterlyGrowth")
    if growth is not None and earnings is not None:
        if growth >= 0.10 and earnings >= 0.10:
            score += 1; notes.append("strong revenue and earnings growth")
        elif growth < 0 and earnings < 0:
            score -= 1; notes.append("revenue and earnings declining")
    margin = num("profitMargins")
    if margin is not None and margin < 0:
        score -= 1; notes.append("loss-making")
    debt = num("debtToEquity")
    if debt is not None and debt > 200:
        score -= 1; notes.append("high leverage")
    elif debt is not None and debt < 50 and margin is not None and margin > 0.08:
        score += 1; notes.append("low debt with healthy margins")
    cash = num("freeCashflow") if num("freeCashflow") is not None else num("operatingCashflow")
    if cash is not None and cash < 0:
        score -= 1; notes.append("negative cash flow")
    pe, peg = num("trailingPE"), num("pegRatio")
    if pe is not None and pe > 0 and peer_pe:
        if pe > 1.5 * peer_pe and (growth or 0) < 0.25:
            score -= 1; notes.append(f"PE {pe:.0f} well above sector median {peer_pe:.0f}")
        elif pe <= 0.9 * peer_pe and (growth or 0) > 0:
            score += 1; notes.append(f"PE {pe:.0f} below sector median {peer_pe:.0f}")
    elif (peg is not None and peg > 3) or (pe is not None and pe > 80 and (growth or 0) < 0.25):
        score -= 1; notes.append("expensive relative to growth")
    elif peg is not None and 0 < peg <= 1.5:
        score += 1; notes.append("reasonable valuation relative to growth")
    return {"score": max(-2, min(2, score)), "available": True, "notes": notes, "data": dict(data)}


def build_research(
    symbol: str, news_items: Sequence[NewsItem], aliases: Sequence[str], price: float,
    fundamentals: Mapping[str, Any] | None = None, load_fundamentals: bool = True,
    news_key: str | None = None,
) -> dict[str, Any]:
    # news_key replaces the ticker for headline matching (discovered stocks are
    # matched by company name only; short tickers collide with ordinary words).
    key = news_key or symbol
    news = assess_news(news_items, key, aliases)
    analysts = assess_analysts(news_items, key, aliases, price)
    if fundamentals is None and load_fundamentals:
        fundamentals = get_fundamentals(symbol)
    fund = assess_fundamentals(
        fundamentals,
        peer_median_pe(symbol, (fundamentals or {}).get("sector")),
    )
    total = news["score"] + analysts["score"] + fund["score"]
    hard_negative = news["score"] < 0 or analysts["score"] < 0
    if hard_negative or total <= AVOID_SCORE:
        verdict = "AVOID"
    elif total >= MIN_RESEARCH_SCORE:
        verdict = "SUPPORTIVE"
    else:
        verdict = "INSUFFICIENT"
    return {
        "verdict": verdict, "score": total, "news": news,
        "analysts": analysts, "fundamentals": fund,
    }


_EXCLUDED_NAME_WORDS = (
    "ETF", "BEES", "FUND", "LIQUID", "NIFTY", "SENSEX", "BOND", "GILT", "GOLD", "SILVER",
)


def build_discovery_universe(
    instruments: Sequence[Mapping[str, Any]], exclude: Sequence[str] | Mapping[str, Any] = (),
) -> tuple[dict[str, int], dict[str, str]]:
    """All plain NSE equities not already watched: symbol -> token, symbol -> name.

    ETFs/funds, series-suffixed symbols and names too short to match headlines
    reliably are skipped."""
    skip = {str(s).upper() for s in exclude}
    tokens: dict[str, int] = {}
    names: dict[str, str] = {}
    for instrument in instruments:
        symbol = str(instrument.get("tradingsymbol", "")).upper()
        name = re.sub(r"\s+", " ", str(instrument.get("name") or "").strip())
        if (
            not symbol or symbol in skip or not symbol.isalnum()
            or instrument.get("exchange") != "NSE"
            or instrument.get("instrument_type") != "EQ"
            or instrument.get("instrument_token") is None
            or any(word in name.upper().split() for word in _EXCLUDED_NAME_WORDS)
        ):
            continue
        core = re.sub(r"\s+(?:limited|ltd\.?)$", "", name, flags=re.IGNORECASE)
        if len(core.split()) < 2 and len(core) < 8:
            continue
        tokens[symbol] = int(instrument["instrument_token"])
        names[symbol] = name
    return tokens, names


def select_candidates(
    news_items: Sequence[NewsItem],
    aliases_by_symbol: Mapping[str, Sequence[str]],
    name_only: Mapping[str, str] | None = None,
    now: float | None = None,
) -> dict[str, str]:
    """Symbols with a fresh positive company catalyst or analyst call.

    Returns symbol -> short reason. Stocks with negative signals are excluded.
    name_only maps discovered symbols to company names matched instead of the ticker.
    """
    current = now if now is not None else time.time()
    # Pre-filter once so scanning thousands of symbols stays cheap.
    news_items = [
        item for item in news_items
        if item.get("published_epoch") is not None
        and 0 <= current - float(item["published_epoch"]) <= 72 * 3600
        and COMPANY_CATEGORIES.intersection(item.get("categories", []))
    ]
    if not news_items:
        return {}
    universe: dict[str, tuple[str, Sequence[str]]] = {
        symbol: (symbol, aliases) for symbol, aliases in aliases_by_symbol.items()
    }
    for symbol, name in (name_only or {}).items():
        universe[symbol] = (name, [name])
    candidates: dict[str, str] = {}
    for symbol, (key, aliases) in universe.items():
        news = assess_news(news_items, key, aliases, now=current)
        analysts = assess_analysts(news_items, key, aliases, 0.0, now=current)
        if news["score"] < 0 or analysts["score"] < 0:
            continue
        if news["score"] > 0 or analysts["score"] > 0:
            reason = news["headlines"][0] if news["headlines"] else "positive analyst call"
            candidates[symbol] = reason
    return candidates


def summarize(research: Mapping[str, Any]) -> str:
    parts = [f"research {research['verdict']} (score {research['score']:+d})"]
    news, analysts, fund = research["news"], research["analysts"], research["fundamentals"]
    if news["headlines"]:
        parts.append(f"news: {news['headlines'][0]}")
    if analysts["avg_target_upside_pct"] is not None:
        parts.append(f"analyst target upside {analysts['avg_target_upside_pct']:.1f}%")
    if fund["notes"]:
        parts.append("fundamentals: " + ", ".join(fund["notes"]))
    return "; ".join(parts)
