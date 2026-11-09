"""
News search through a local SearXNG container (replaces the Google News RSS fetcher).

    36 metric queries per company ──► SearXNG ──► DuckDuckGo News, Brave News, Bing News
                                                  │
                                                  ▼
                              real article URLs (no redirect decoding step), some with a date

Why it is built this way (all checked against the user's own container):

* Short queries, not the Google-style OR chains in metrics.yaml: each metric is searched as
  "<company> <key phrase>" (see short_queries). The OR chains returned 0 results on DuckDuckGo
  and "parsing error" on Bing for many metrics in the first real run.
* Only growth metrics are searched by default (news_metrics): the agent never reads per-metric
  risk sentiment.
* Choosing the articles for a metric (select_results) works on ALL results of a query (40-80 in
  the first real run), never on a pre-cut list:
    C  one story once: same URL, or the same title on another site (fool / nasdaq / aol)
    A  results that mention one of the metric's phrases in the title or snippet are ranked
       first. Nothing is dropped for NOT mentioning it (a relevant article may not name the
       metric in its headline); the real relevance is judged on the full text after extraction
       (extraction/metric_relevance.py) and used as a weight in the aggregation
    B  within each of those two groups: dated results first, newest first, then undated ones
  then the top `max_per_metric` (default 10) are kept. 10, not 3, because some pages cannot be
  read by the extractor (paywalls, bot walls) and those never get a sentiment score.
  Across metrics, the same story is one article with several metric tags, and
  store_metric_news also merges it with a copy stored on an earlier run.
* NEVER send `time_range`: SearXNG then skips DuckDuckGo and Brave and only Bing answers.
  Old articles are dropped here instead (MAX_AGE_DAYS) and the sentiment time decay
  down-weights the rest.
* Only DuckDuckGo returns a publish date. Brave and Bing results have none, so those
  articles are stored undated and get their date from the page during extraction
  (extraction/article_extractor.find_publish_date).
* Google News and Reuters block automated access and are removed in settings.yml.
* The same query is answered from data/news_queries/<day>/ if it was already asked today,
  and queries are spaced out, so a re-run does not hit the search engines again.

Run it from scripts/data-prep/fetch_news.py. This module never talks to SearXNG unless called.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ..config import ROOT_DIR, searxng_url
from ..extraction.metric_relevance import mentions_any, metric_phrases
from ..ingestion.dedup import canonicalize_url, title_key

log = logging.getLogger(__name__)

ENGINES = "duckduckgo news,brave.news,bing news"
MAX_AGE_DAYS = 365                      # dated articles older than this are dropped
# Pause after each query that really hit SearXNG. 2-4 s was too fast: Brave answered "too many
# requests" from the 6th query on and SearXNG then suspends that engine (for about an hour).
QUERY_DELAY_SECONDS = (8.0, 14.0)
REQUEST_TIMEOUT_SECONDS = 60
CACHE_DIR = ROOT_DIR / "data" / "news_queries"
MAX_QUERIES_PER_METRIC = 2              # first key phrase, then the second only if too few articles
ARTICLES_PER_METRIC = 10                # kept per metric; some pages will fail extraction
QUERY_WINDOW_LABEL = "searxng"          # stored in matched_metrics[].query_window


def parse_published(value, now: datetime | None = None) -> datetime | None:
    """SearXNG's publishedDate as an aware UTC datetime. None if missing, unparsable or in the future."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    now = now or datetime.now(timezone.utc)
    return None if parsed > now + timedelta(days=1) else parsed


# ------------------------------------------------------------------ one query
CACHE_FORMAT = "v2"  # v2 stores the snippet ("content") the relevance filter needs; v1 files are ignored


def _cache_file(query: str, engines: str, day: str) -> Path:
    key = hashlib.sha1(f"{CACHE_FORMAT}|{engines}|{query}".encode("utf-8")).hexdigest()
    return CACHE_DIR / day / f"{key}.json"


def search(query: str, base_url: str | None = None, engines: str = ENGINES,
           use_cache: bool = True) -> tuple[list[dict], bool]:
    """(results, answered_from_cache). Each result: url, title, content (snippet), publishedDate, engines."""
    base_url = (base_url or searxng_url()).rstrip("/")
    cache = _cache_file(query, engines, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    if use_cache and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8")), True

    try:
        response = requests.get(
            f"{base_url}/search",
            params={"q": query, "format": "json", "engines": engines},  # no time_range, see module docstring
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.ConnectionError as exc:
        raise RuntimeError(
            f"Cannot reach SearXNG at {base_url}. Start the container (docker ps in Ubuntu) "
            f"or set SEARXNG_URL in .env."
        ) from exc
    if response.status_code == 403:
        raise RuntimeError("SearXNG refused format=json: add 'json' under search.formats in its settings.yml.")
    response.raise_for_status()

    data = response.json()
    unresponsive = data.get("unresponsive_engines")
    if unresponsive:
        log.warning("SearXNG engines that did not answer: %s", unresponsive)
    results = [
        {"url": r.get("url"), "title": r.get("title"), "content": r.get("content") or "",
         "publishedDate": r.get("publishedDate"), "engines": r.get("engines") or [r.get("engine")]}
        for r in data.get("results", [])
    ]
    # Only a complete answer is cached. A query that an engine failed on (rate limit, parsing error)
    # or that returned nothing would otherwise stay degraded for the rest of the day.
    if results and not unresponsive:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(results), encoding="utf-8")
    return results, False


# ----------------------------------------------------------- all metric queries
def candidate_results(raw: list[dict], phrases: list[str] | None = None,
                      now: datetime | None = None) -> list[dict]:
    """Every usable result of ONE query, in SearXNG's order, before anything is cut to the top N.
    Drops entries without url/title, dated articles older than MAX_AGE_DAYS and repeats of the
    same URL or the same story on another site (C). Does NOT drop anything for its keywords:
    each candidate only gets `mentions_metric` (title or snippet names one of the phrases) for ranking."""
    now = now or datetime.now(timezone.utc)
    oldest = now - timedelta(days=MAX_AGE_DAYS)
    out, seen_urls, seen_titles = [], set(), set()
    for r in raw:
        url, title = r.get("url"), (r.get("title") or "").strip()
        if not url or not title:
            continue
        ukey, tkey = canonicalize_url(url), title_key(title)
        if ukey in seen_urls or (tkey and tkey in seen_titles):
            continue
        published = parse_published(r.get("publishedDate"), now)
        if published is not None and published < oldest:
            continue
        seen_urls.add(ukey)
        if tkey:
            seen_titles.add(tkey)
        out.append({"url": url, "title": title, "published_at": published, "engines": r.get("engines") or [],
                    "mentions_metric": mentions_any(f"{title} {r.get('content') or ''}", phrases)})
    return out


def rank_results(candidates: list[dict]) -> list[dict]:
    """(A) results that name the metric in title or snippet first, then the others;
    (B) inside each group dated results first, newest first, then undated in SearXNG's order.
    Only DuckDuckGo returns dates, and its results sit lower in SearXNG's merged list."""
    def by_date(group):
        dated = sorted((c for c in group if c["published_at"]), key=lambda c: c["published_at"], reverse=True)
        return dated + [c for c in group if not c["published_at"]]
    return (by_date([c for c in candidates if c.get("mentions_metric")])
            + by_date([c for c in candidates if not c.get("mentions_metric")]))


def select_results(raw: list[dict], max_results: int, now: datetime | None = None,
                   phrases: list[str] | None = None) -> list[dict]:
    """The top `max_results` of one query: C applied to ALL results, then ranked (A, B), then cut."""
    return rank_results(candidate_results(raw, phrases, now))[:max_results]


def short_queries(company_search_name: str, search_template: str,
                  limit: int = MAX_QUERIES_PER_METRIC) -> list[str]:
    """Short news queries for one metric: the company name plus one key phrase each, in the order
    the template lists them (the first phrase is the metric's core term).

    The templates in metrics.yaml are Google-style OR chains. Checked on the user's container:
    'MercadoLibre ("return on assets" OR "asset efficiency" OR "asset utilization")' returns 0
    results on DuckDuckGo News, 'MercadoLibre return on assets' returns 4 (all dated).
    The phrase is sent without quotes, which also matched more."""
    phrases = metric_phrases(search_template)
    if not phrases:  # a template without quoted phrases is used as written
        return [search_template.format(COMPANY=company_search_name)]
    return [f"{company_search_name} {p}" for p in phrases[:limit]]


def news_metrics(metrics: list[dict], include_risk: bool = False) -> list[dict]:
    """The metrics worth a news search. Risk metrics are skipped by default: the screening agent
    only reads per-metric sentiment for the 25 growth metrics, so risk queries would only cost
    time and rate-limit budget (11 of 36 queries per company)."""
    return [m for m in metrics if include_risk or m.get("theme") != "risk"]


def collect_all_metric_articles(company_search_name: str, metrics: list[dict],
                                max_per_metric: int = ARTICLES_PER_METRIC,
                                base_url: str | None = None, engines: str = ENGINES,
                                search_fn=search, sleep_fn=time.sleep, use_cache: bool = True,
                                max_queries_per_metric: int = MAX_QUERIES_PER_METRIC) -> list[dict]:
    """Runs the short queries of every metric for one company: the first key phrase, then the next
    one only if fewer than `max_per_metric` articles were found. One article per URL, with all the
    metrics it matched. Each item: title, url, published_at (aware datetime or None),
    matched_metrics, engines."""
    articles: dict[str, dict] = {}
    story_of_title: dict[str, str] = {}   # (C) title key -> key of the article already holding that story
    for metric in metrics:
        phrases = metric_phrases(metric["search_template"])
        chosen, seen = [], set()
        for query in short_queries(company_search_name, metric["search_template"], max_queries_per_metric):
            if search_fn is search:
                raw, from_cache = search(query, base_url=base_url, engines=engines, use_cache=use_cache)
            else:
                raw, from_cache = search_fn(query, base_url=base_url, engines=engines)
            candidates = candidate_results(raw, phrases)          # C on ALL results, nothing dropped for keywords
            new = 0
            for c in rank_results(candidates):                    # A, B ranking, then cut to max_per_metric
                keys = {canonicalize_url(c["url"]), "title:" + title_key(c["title"])}
                if not (keys & seen) and len(chosen) < max_per_metric:
                    seen |= keys
                    chosen.append({**c, "query": query})
                    new += 1
            mentioning = sum(1 for c in candidates if c["mentions_metric"])
            log.info("[%s] '%s': %d results, %d distinct, %d name the metric in title/snippet, kept %d%s",
                     metric["metric_id"], query, len(raw), len(candidates), mentioning, new,
                     " [cache]" if from_cache else "")
            if not from_cache:
                sleep_fn(random.uniform(*QUERY_DELAY_SECONDS))
            if len(chosen) >= max_per_metric:
                break
        dated = sum(1 for c in chosen if c["published_at"])
        log.info("[%s] kept %d articles (%d dated)", metric["metric_id"], len(chosen), dated)

        for c in chosen:
            tkey = title_key(c["title"])
            key = story_of_title.get(tkey) or canonicalize_url(c["url"])   # (C) same story found earlier
            if tkey:
                story_of_title.setdefault(tkey, key)
            article = articles.setdefault(key, {
                "title": c["title"], "url": c["url"], "published_at": c["published_at"],
                "matched_metrics": [], "engines": set(),
            })
            if article["published_at"] is None and c["published_at"] is not None:
                article["published_at"] = c["published_at"]
            article["engines"].update(e for e in c["engines"] if e)
            if metric["metric_id"] not in {m["metric_id"] for m in article["matched_metrics"]}:
                article["matched_metrics"].append(
                    {"metric_id": metric["metric_id"], "query_window": QUERY_WINDOW_LABEL, "query": c["query"]})

    out = list(articles.values())
    for a in out:
        a["engines"] = sorted(a["engines"])
    return out
