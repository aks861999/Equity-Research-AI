"""
Entry point: `python -m equity_pipeline.main --ticker MELI`

Ties together the three independent fetchers (sec_edgar, yahoo_finance,
google_news) with the ingestion layer (upsert + couchdb_client), keyed off a
single ticker looked up in config/companies.yaml. This is the piece that
did not exist before — each fetcher worked standalone, but nothing called
all three and wrote the results into CouchDB for a given company.
"""
import argparse
import asyncio
import logging

from .config import db_settings, gnews_settings, get_company, load_metrics
from .db.postgres_client import PostgresClient
from .ingestion import queries
from .ingestion.upsert import upsert_article, upsert_filing
from .sources.google_news import collect_all_metric_articles, decode_unique_links
from .sources.sec_edgar import fetch_sec_filings
from .sources.yahoo_finance import fetch_yahoo_news

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("main")

def get_db() -> PostgresClient:
    db = PostgresClient(**db_settings())
    db.ensure_db()
    queries.create_indexes(db) # You can leave this, it won't hurt, or remove it
    return db


def ingest_sec_filings(db, company: dict):
    if not company.get("sec_form_type"):
        # No sec_form_type in companies.yaml -> this ticker is intentionally
        # excluded from sec_edgar.py (e.g. not a US EDGAR filer).
        logger.info("[sec_edgar] %s: skipped (no sec_form_type configured)", company["ticker"])
        return 0
    filings = fetch_sec_filings(company["ticker"], company["sec_form_type"])
    for filing in filings:
        upsert_filing(db, company["ticker"], filing)
    logger.info("[sec_edgar] %s: upserted %d filing sections", company["ticker"], len(filings))
    return len(filings)


def ingest_yahoo_news(db, company: dict):
    articles = fetch_yahoo_news(company["ticker"])
    for article in articles:
        upsert_article(
            db, company["ticker"], source="yahoo_finance", metric_scope="general",
            title=article["title"], url=article["url"],
            published_at=article["published_at"], matched_metrics=[],
        )
    logger.info("[yahoo_finance] %s: upserted %d articles", company["ticker"], len(articles))
    return len(articles)


def ingest_google_news(db, company: dict, metrics: list[dict]):
    collected = collect_all_metric_articles(
        company["primary_search_name"], 
        metrics, 
        max_per_metric=1, 
        api_key=gnews_settings()["api_key"] # Pass the API key here
    )
    
    # decode_unique_links no longer decodes anything, just formats the list
    decoded = decode_unique_links(collected)
    
    for article in decoded:
        upsert_article(
            db, company["ticker"], source="google_news", metric_scope="metric",
            title=article["title"], url=article["url"], 
            google_link=article.get("google_link"), 
            published_at=article["published_at"], matched_metrics=article["matched_metrics"],
        )
    logger.info("[google_news] %s: upserted %d unique articles across %d metrics",
                company["ticker"], len(decoded), len(metrics))
    return len(decoded)


def ingest_company(db, ticker: str, metrics: list[dict]):
    company = get_company(ticker)
    logger.info("=== Ingesting %s (%s) ===", company["ticker"], company["primary_search_name"])
    ingest_sec_filings(db, company)
    ingest_yahoo_news(db, company)
    ingest_google_news(db, company, metrics)


def main():
    parser = argparse.ArgumentParser(description="Ingest news/filings for one or all companies")
    parser.add_argument("--ticker", help="Single ticker to ingest (must exist in companies.yaml). Omit to run all.")
    parser.add_argument("--run-extraction", action="store_true",
                         help="Also run the pending-article body-text extraction pass after ingesting.")
    parser.add_argument("--run-sentiment", action="store_true",
                         help="Run FinBERT sentiment analysis on extracted articles.")
    args = parser.parse_args()

    db = get_db()
    metrics = load_metrics()

    if args.ticker:
        ingest_company(db, args.ticker, metrics)
    else:
        from .config import load_companies
        for company in load_companies():
            ingest_company(db, company["ticker"], metrics)

    if args.run_extraction:
        from .extraction.run_extraction import run_pending_extractions
        result = asyncio.run(run_pending_extractions(db, batch_limit=100000))
        logger.info("[extraction] %s", result)

    if args.run_sentiment:
        from .extraction.run_sentiment import run_pending_sentiments
        result = run_pending_sentiments(db, batch_limit=100000)
        logger.info("[sentiment] %s", result)

if __name__ == "__main__":
    main()