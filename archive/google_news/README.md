# Archived: Google News fetcher

Retired. The pipeline now gets its per-metric news from the local SearXNG container
(`src/equity_pipeline/sources/searxng_news.py`). This folder keeps the old fetcher for reference.

| | Google News (this folder) | SearXNG (current) |
|---|---|---|
| Source | Google News RSS, one query per metric | DuckDuckGo news, Brave news, Bing news through one local container |
| Article links | redirect links that had to be decoded one by one (`googlenewsdecoder`), 10 to 15 s pause per decode | direct URLs |
| Publish date | RSS date | engine date, else read from the page |
| Why retired | slow, fragile decoding, rate limits | faster, direct links, dates, per-day query cache |

## Files

| File | What it is |
|---|---|
| `google_news.py` | the fetcher, unchanged apart from the archive note in its header |
| `requirements.txt` | the extra package it needs (`feedparser` is already in the main requirements) |

## Using it again

1. Copy `google_news.py` back to `src/equity_pipeline/sources/` (it imports `..ingestion.time_utils`, so it only works from there).
2. `pip install -r archive/google_news/requirements.txt`
3. Call it the way `searxng_news.collect_all_metric_articles` is called in `agent/pipeline_runner.py` and in
   `scripts/data-prep/fetch_news.py`; the stored article format is the same (`source`, `matched_metrics`, `published_at`).
4. Old database rows from this fetcher have `source = "google_news"`; the dashboard still labels them "News (Google, old)".
