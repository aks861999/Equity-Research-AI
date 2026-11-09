"""
Yahoo Finance news fetcher — general/company-level scope only (no metric
mapping attempted; see schema doc for why). Adapted from your 2_yfin.py to
return structured data instead of printing.

Yahoo sometimes cuts a response short (curl error 18, "end of response with N bytes
missing") or refuses for a moment. The request is retried a few times; if it still fails,
the company gets no Yahoo articles for this run and a warning is logged, instead of the
whole ingestion run crashing.
"""
import logging
import time

import yfinance as yf

log = logging.getLogger(__name__)

ATTEMPTS = 3
BACKOFF_SECONDS = 4.0  # 4 s, then 8 s


def _news_items(ticker_symbol: str) -> tuple[list[dict], bool]:
    """(items, ok). ok is False when every attempt failed: "Yahoo had no news" and "Yahoo could not be
    reached" are different things and the dashboard tells them apart."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return yf.Ticker(ticker_symbol).news or [], True
        except Exception as exc:  # network / Yahoo-side errors; yfinance raises several types
            if attempt == ATTEMPTS:
                log.warning("[yahoo_finance] %s: no news this run after %d attempts (%s)",
                            ticker_symbol, ATTEMPTS, exc)
                return [], False
            log.info("[yahoo_finance] %s: attempt %d failed (%s), retrying", ticker_symbol, attempt, exc)
            time.sleep(BACKOFF_SECONDS * attempt)
    return [], False


def fetch_yahoo_news(ticker_symbol: str, max_articles: int = 20, stats: dict | None = None):
    """`stats`, if given, is filled with ok (Yahoo answered) and fetched (headlines returned)."""
    articles = []
    items, ok = _news_items(ticker_symbol)

    for item in items:
        content = item.get("content", {})

        # Prefer canonicalUrl over clickThroughUrl — more stable for dedup
        # (clickThroughUrl can carry tracking params that vary run to run).
        url = (
            (content.get("canonicalUrl") or {}).get("url")
            or (content.get("clickThroughUrl") or {}).get("url")
        )
        if not url:
            continue

        published_at = content.get("pubDate") or item.get("providerPublishTime")

        articles.append({
            "title": content.get("title"),
            "url": url,
            "published_at": published_at,
            "provider": (content.get("provider") or {}).get("displayName"),
        })

    articles.sort(key=lambda a: a["published_at"] or "", reverse=True)
    articles = articles[:max_articles]
    if stats is not None:
        stats.update({"ok": ok, "fetched": len(articles)})
    return articles
