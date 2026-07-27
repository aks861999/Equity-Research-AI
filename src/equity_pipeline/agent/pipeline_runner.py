"""
"Always fresh" mode: runs the same chain of steps `main.py`'s
--run-extraction / --run-sentiment / --run-aggregation / --run-fundamentals /
--run-risk-fundamentals flags trigger, but inline, synchronously, scoped to
a single ticker, immediately before the screening agent answers.

This is a pre-processing step called from run_screening() BEFORE the
PydanticAI Agent is even constructed -- it does not add a third tool to the
agent, so the task's "exactly two tools" constraint is untouched. Think of
it as: "make sure the two tools have something fresh to read/fetch", not
part of the agent's own reasoning loop.

Deliberate deviation from main.py: main.py's ingest_company() currently has
Google News ingestion commented out (`#ingest_google_news(...)`), presumably
because it's the slowest/most rate-limited step. Since the growth question
depends on metric_scores (which only exist for Google-News-matched
articles), this runner turns Google News ingestion back on. If you'd rather
keep it off (faster, but growth-question sentiment stays whatever was last
computed), set `include_google_news=False`.

Performance note: SEC EDGAR and Google News both have deliberate
rate-limit sleeps (see sources/sec_edgar.py REQUEST_DELAY,
sources/google_news.py), and FinBERT scoring is a local model call per
article. A single ticker refresh can realistically take anywhere from
~15 seconds (little new data) to a couple of minutes (many new filings/
articles). Expose this as a toggle in any PM-facing UI rather than forcing
it on every click.
"""
from __future__ import annotations

import asyncio
import logging

from ..config import get_company, load_metrics
from ..extraction.run_aggregation import run_metric_aggregation
from ..extraction.run_extraction import run_pending_extractions
from ..extraction.run_growth_fundamentals import run_fundamental_extraction
from ..extraction.run_risk_fundamentals import run_risk_fundamental_extraction
from ..extraction.run_sentiment import run_pending_sentiments
from ..ingestion.dedup import article_doc_id
from ..ingestion.upsert import upsert_article, upsert_filing
from ..sources.google_news import collect_all_metric_articles, decode_unique_links
from ..sources.sec_edgar import fetch_sec_filings
from ..sources.yahoo_finance import fetch_yahoo_news

logger = logging.getLogger("agent.pipeline_runner")


def _ingest_sec(db, company: dict) -> int:
    if not company.get("sec_form_type"):
        return 0
    filings = fetch_sec_filings(company["ticker"], company["sec_form_type"], db=db)
    for filing in filings:
        upsert_filing(db, company["ticker"], filing)
    return len(filings)


def _ingest_yahoo(db, company: dict) -> int:
    articles = fetch_yahoo_news(company["ticker"])
    upserted = 0
    for article in articles:
        doc_id = article_doc_id(company["ticker"], article["url"])
        if db.get(doc_id):
            continue
        upsert_article(
            db, company["ticker"], source="yahoo_finance", metric_scope="general",
            title=article["title"], url=article["url"],
            published_at=article["published_at"], matched_metrics=[],
        )
        upserted += 1
    return upserted


def _ingest_google_news(db, company: dict, metrics: list[dict]) -> int:
    collected = collect_all_metric_articles(company["primary_search_name"], metrics, max_per_metric=1)
    decoded = decode_unique_links(collected, db=db, ticker=company["ticker"])
    for article in decoded:
        upsert_article(
            db, company["ticker"], source="google_news", metric_scope="metric",
            title=article["title"], url=article["url"],
            google_link=article.get("google_link"),
            published_at=article["published_at"], matched_metrics=article["matched_metrics"],
        )
    return len(decoded)


def run_live_pipeline(db, ticker: str, include_google_news: bool = True) -> dict:
    """Runs, in order, for a single ticker: SEC EDGAR ingest -> Yahoo Finance
    ingest -> (optionally) Google News per-metric ingest -> pending body-text
    extraction -> pending FinBERT sentiment scoring -> metric-score
    aggregation -> growth fundamentals (CSV) -> risk fundamentals (CSV).

    Note: run_pending_extractions / run_pending_sentiments / run_metric_
    aggregation operate on whatever is globally pending/scored across ALL
    tickers (that's how the underlying functions in extraction/ are
    written), not just this one -- harmless, but means a refresh can also
    pick up backlog from other tickers.
    """
    company = get_company(ticker)  # raises if out of scope, before anything runs
    metrics = load_metrics()
    counts: dict = {"ticker": ticker}

    logger.info("[live_pipeline] %s: SEC EDGAR ingest...", ticker)
    counts["sec_filings_ingested"] = _ingest_sec(db, company)

    logger.info("[live_pipeline] %s: Yahoo Finance ingest...", ticker)
    counts["yahoo_articles_ingested"] = _ingest_yahoo(db, company)

    if include_google_news:
        logger.info("[live_pipeline] %s: Google News per-metric ingest...", ticker)
        counts["google_news_articles_ingested"] = _ingest_google_news(db, company, metrics)
    else:
        counts["google_news_articles_ingested"] = "skipped"

    logger.info("[live_pipeline] %s: body-text extraction...", ticker)
    counts["extraction"] = asyncio.run(run_pending_extractions(db, batch_limit=200))

    logger.info("[live_pipeline] %s: FinBERT sentiment scoring...", ticker)
    counts["sentiment"] = run_pending_sentiments(db, batch_limit=2000)

    logger.info("[live_pipeline] %s: metric-score aggregation...", ticker)
    counts["aggregation"] = run_metric_aggregation(db)

    logger.info("[live_pipeline] %s: growth fundamentals (CSV)...", ticker)
    counts["growth_fundamentals"] = run_fundamental_extraction(db)

    logger.info("[live_pipeline] %s: risk fundamentals (CSV)...", ticker)
    counts["risk_fundamentals"] = run_risk_fundamental_extraction(db)

    logger.info("[live_pipeline] %s complete: %s", ticker, counts)
    return counts