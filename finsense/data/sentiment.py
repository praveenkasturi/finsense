from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger("finsense.sentiment")

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore[assignment]

POSITIVE_PHRASES: list[tuple[str, float]] = [
    ("beats expectations", 1.5),
    ("record revenue", 1.4),
    ("strong earnings", 1.3),
    ("raised guidance", 1.5),
    ("upgrade", 1.2),
    ("outperform", 1.1),
    ("bullish", 1.0),
    ("beat estimates", 1.4),
    ("all-time high", 1.2),
    ("positive surprise", 1.3),
    ("growth accelerat", 1.2),
    ("margin expan", 1.1),
    ("buyback", 0.8),
    ("dividend increase", 0.9),
    ("strong demand", 1.0),
    ("profit", 0.6),
    ("surge", 0.9),
    ("rally", 0.7),
    ("beat", 0.7),
    ("growth", 0.5),
    ("strong", 0.5),
    ("record", 0.5),
    ("win", 0.4),
]

NEGATIVE_PHRASES: list[tuple[str, float]] = [
    ("misses expectations", 1.5),
    ("lowered guidance", 1.5),
    ("revenue miss", 1.4),
    ("downgrade", 1.2),
    ("bearish", 1.0),
    ("investigation", 1.1),
    ("lawsuit", 1.0),
    ("sec probe", 1.3),
    ("bankruptcy", 1.5),
    ("default risk", 1.4),
    ("margin compress", 1.1),
    ("layoff", 0.9),
    ("weak guidance", 1.2),
    ("profit warning", 1.3),
    ("supply chain", 0.7),
    ("miss", 0.7),
    ("weak", 0.6),
    ("drop", 0.5),
    ("decline", 0.5),
    ("loss", 0.6),
    ("warning", 0.5),
    ("crash", 0.9),
    ("sell-off", 0.8),
]

NEGATION_WORDS = {"not", "no", "never", "neither", "hardly", "barely", "didn't", "doesn't", "won't", "isn't"}


def _phrase_sentiment(text: str) -> float:
    lowered = text.lower()
    words = set(re.findall(r"\b\w+\b", lowered))
    has_negation = bool(words & NEGATION_WORDS)
    score = 0.0
    for phrase, weight in POSITIVE_PHRASES:
        if phrase in lowered:
            score += weight * (-0.5 if has_negation else 1.0)
    for phrase, weight in NEGATIVE_PHRASES:
        if phrase in lowered:
            score -= weight * (-0.5 if has_negation else 1.0)
    return score


def score_headlines(headlines: list[str]) -> float:
    if not headlines:
        return 0.0
    total = sum(_phrase_sentiment(h) for h in headlines)
    normalized = total / max(1, len(headlines))
    return max(-1.0, min(1.0, normalized * 0.35))


def fetch_newsapi_headlines(
    ticker: str,
    api_key: str,
    max_articles: int = 15,
) -> tuple[float, list[dict[str, str]], list[str]]:
    if not api_key or not _requests:
        return 0.0, [], []
    try:
        resp = _requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": ticker,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": max_articles,
                "apiKey": api_key,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("NewsAPI returned %s for %s", resp.status_code, ticker)
            return 0.0, [], []
        data = resp.json()
        articles = data.get("articles", [])
        headlines: list[str] = []
        items: list[dict[str, str]] = []
        for a in articles:
            title = str(a.get("title", "")).strip()
            if not title or title == "[Removed]":
                continue
            headlines.append(title)
            items.append({
                "title": title,
                "publisher": str(a.get("source", {}).get("name", "")),
                "link": str(a.get("url", "")),
                "published_utc": str(a.get("publishedAt", "")),
                "description": str(a.get("description", ""))[:200],
            })
        sentiment = score_headlines(headlines)
        return sentiment, items, headlines
    except Exception as exc:
        logger.warning("NewsAPI error for %s: %s", ticker, exc)
        return 0.0, [], []


def yfinance_news_sentiment(
    ticker_obj: Any,
) -> tuple[float, list[dict[str, str]], list[str]]:
    try:
        news = ticker_obj.news or []
    except Exception:
        news = []
    headlines: list[str] = []
    items: list[dict[str, str]] = []
    for item in news[:12]:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        headlines.append(title)
        items.append({
            "title": title,
            "publisher": str(item.get("publisher", "")),
            "link": str(item.get("link", "")),
            "published_utc": str(item.get("providerPublishTime", "")),
        })
    sentiment = score_headlines(headlines)
    return sentiment, items, headlines
