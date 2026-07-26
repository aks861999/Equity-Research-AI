"""
Yahoo Finance news fetcher — general/company-level scope only (no metric
mapping attempted; see schema doc for why). Adapted from your 2_yfin.py to
return structured data instead of printing.
"""
import yfinance as yf


def fetch_yahoo_news(ticker_symbol: str, max_articles: int = 20):
    ticker = yf.Ticker(ticker_symbol)
    articles = []

    for item in ticker.news:
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
    return articles[:max_articles]
