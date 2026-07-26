"""
Google News fetcher — per-metric, per-company.

Change from your original 1_google_news_fetcher.py: that script called
gnewsdecoder() once per article per query, with a 10-15s sleep between calls
to stay polite. Run straight, across 25 metrics x 20 articles x 4 companies,
that's ~2000 decode calls -> 2000 x ~12s ~= 6-7 hours, most of it spent
re-decoding the *same* handful of articles over and over (your own design doc
points out one earnings article typically satisfies 8-10 metric queries).

Fix: collect raw (undecoded) RSS entries for all 25 metrics first, dedup on
the raw Google redirect link, and only decode each unique link once. This
typically cuts decode calls by 5-10x for very little added complexity.
"""
import time
import random
import calendar
import urllib.parse
from datetime import datetime, timezone

import feedparser
from googlenewsdecoder import gnewsdecoder

from ..ingestion.time_utils import to_iso

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
WIDEN_WINDOWS = ["when:1m", "when:3m", "when:1y"]
MIN_RESULTS_BEFORE_WIDENING = 5


def _parse_pubdate(entry):
    """feedparser's published_parsed is a UTC struct_time; convert with
    calendar.timegm (not time.mktime, which assumes local time)."""
    if getattr(entry, "published_parsed", None):
        ts = calendar.timegm(entry.published_parsed)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def fetch_metric_rss(company_search_name: str, search_template: str, window: str):
    query = f"{search_template.format(COMPANY=company_search_name)} {window}"
    safe_query = urllib.parse.quote(query)
    url = GOOGLE_NEWS_RSS.format(query=safe_query)
    feed = feedparser.parse(url)
    return [
        {
            "title": e.title,
            "google_link": e.link,
            "published_at": _parse_pubdate(e),
        }
        for e in feed.entries
    ]


def fetch_metric_with_widening(company_search_name: str, search_template: str,
                                max_results: int = 20):

    print()
    """Per your design doc's guidance: widen when:1m -> when:3m -> when:1y if
    the trailing window returns too few results."""
    entries, window_used = [], WIDEN_WINDOWS[0]
    for window in WIDEN_WINDOWS:
        entries = fetch_metric_rss(company_search_name, search_template, window)
        window_used = window
        if len(entries) >= MIN_RESULTS_BEFORE_WIDENING or window == WIDEN_WINDOWS[-1]:
            break
        time.sleep(0.3)
    return entries[:max_results], window_used


def collect_all_metric_articles(company_search_name: str, metrics: list, max_per_metric: int = 20):
    """
    Runs every metric's search string for one company. Returns a dict keyed by
    the raw Google redirect link (pre-decode), so the same story hit by
    multiple metric queries is collapsed to one entry with multiple
    matched_metrics before any decoding happens.
    """
    collected = {}
    for metric in metrics:
        entries, window_used = fetch_metric_with_widening(
            company_search_name, metric["search_template"], max_per_metric
        )
        for e in entries:
            key = e["google_link"]
            if key not in collected:
                collected[key] = {
                    "title": e["title"],
                    "published_at": e["published_at"],
                    "matched_metrics": [],
                }
            existing_ids = {m["metric_id"] for m in collected[key]["matched_metrics"]}
            if metric["metric_id"] not in existing_ids:
                collected[key]["matched_metrics"].append({
                    "metric_id": metric["metric_id"],
                    "query_window": window_used,
                })
        time.sleep(0.4)  # polite pause between RSS queries (cheap, no decode yet)
    return collected


def decode_unique_links(collected: dict, decode_delay_range=(2, 4)):
    """Decode each unique link exactly once. Falls back to the raw Google
    redirect link if decoding fails, so a decode hiccup doesn't drop the
    article — it just means dedup keys on the (still valid) redirect URL."""
    decoded = []
    items = list(collected.items())
    for i, (google_link, meta) in enumerate(items):
        try:
            result = gnewsdecoder(google_link)
            real_url = result["decoded_url"] if result.get("status") else google_link
        except Exception:
            real_url = google_link

        decoded.append({
            "title": meta["title"],
            "url": real_url,
            "published_at": to_iso(meta["published_at"]),
            "matched_metrics": meta["matched_metrics"],
        })

        if i < len(items) - 1:
            time.sleep(random.uniform(*decode_delay_range))
    return decoded
