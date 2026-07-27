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

from .config import db_settings, get_company, load_metrics
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
        logger.info("[sec_edgar] %s: skipped (no sec_form_type configured)", company["ticker"])
        return 0
    
    # Add db=db to the end of the call
    filings = fetch_sec_filings(company["ticker"], company["sec_form_type"], db=db)
    
    for filing in filings:
        upsert_filing(db, company["ticker"], filing)
    logger.info("[sec_edgar] %s: upserted %d filing sections", company["ticker"], len(filings))
    return len(filings)


# Import article_doc_id at the top of main.py
from .ingestion.dedup import article_doc_id

def ingest_yahoo_news(db, company: dict):
    articles = fetch_yahoo_news(company["ticker"])
    skipped = 0
    upserted = 0
    
    for article in articles:
        # Calculate the ID the article WOULD have
        doc_id = article_doc_id(company["ticker"], article["url"])
        
        # Check if it already exists in the database
        if db.get(doc_id):
            skipped += 1
            continue # Skip the upsert entirely!
            
        upsert_article(
            db, company["ticker"], source="yahoo_finance", metric_scope="general",
            title=article["title"], url=article["url"],
            published_at=article["published_at"], matched_metrics=[],
        )
        upserted += 1
        
    logger.info("[yahoo_finance] %s: upserted %d new articles, skipped %d existing", 
                company["ticker"], upserted, skipped)
    return upserted


def ingest_google_news(db, company: dict, metrics: list[dict]):
    # Removed the api_key, back to standard Google News RSS
    collected = collect_all_metric_articles(
        company["primary_search_name"], 
        metrics, 
        max_per_metric=1
    )
    
    # MUST pass db and ticker so it skips decoding for articles you already have!
    decoded = decode_unique_links(collected, db=db, ticker=company["ticker"])
    
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
                         help="Run ONLY the pending-article body-text extraction pass (skips ingestion).")
    parser.add_argument("--run-sentiment", action="store_true",
                         help="Run ONLY FinBERT sentiment analysis on extracted articles (skips ingestion).")
    parser.add_argument("--run-aggregation", action="store_true",
                        help="Aggregate article sentiments into final PM metric scores.")
    parser.add_argument("--run-fundamentals", action="store_true",
                        help="Calculate fundamental metrics from CSV files and store in DB.")

    parser.add_argument("--run-summary", action="store_true",
                         help="Use LLM to extract key positives/negatives from recent news.")

    parser.add_argument("--run-risk-fundamentals", action="store_true",
                        help="Calculate RISK fundamental metrics from CSV files and store in DB.")
    
    args = parser.parse_args()

    db = get_db()
    metrics = load_metrics()

    # STEP 1: Ingestion (Only runs if you DO NOT pass --run-extraction or --run-sentiment)
    if not args.run_extraction and not args.run_sentiment and not args.run_aggregation and not args.run_fundamentals and not args.run_summary and not args.run_risk_fundamentals:

        if args.ticker:
            ingest_company(db, args.ticker, metrics)
        else:
            from .config import load_companies
            for company in load_companies():
                ingest_company(db, company["ticker"], metrics)

    # STEP 2: Extraction (Runs only if you pass --run-extraction)
    if args.run_extraction:
        from .extraction.run_extraction import run_pending_extractions
        result = asyncio.run(run_pending_extractions(db, batch_limit=100000))
        logger.info("[extraction] %s", result)

    # STEP 3: Sentiment (Runs only if you pass --run-sentiment)
    if args.run_sentiment:
        from .extraction.run_sentiment import run_pending_sentiments
        result = run_pending_sentiments(db, batch_limit=100000)
        logger.info("[sentiment] %s", result)

    # STEP 4: Aggregation (Runs only if you pass --run-aggregation)
    if args.run_aggregation:
        from .extraction.run_aggregation import run_metric_aggregation
        result = run_metric_aggregation(db)
        logger.info("[aggregation] %s", result)

    # STEP 5: Fundamentals (Runs only if you pass --run-fundamentals)
    if args.run_fundamentals:
        from .extraction.run_growth_fundamentals import run_fundamental_extraction
        result = run_fundamental_extraction(db)
        logger.info("[fundamentals] %s", result)

    # STEP 6: LLM Summary (Runs only if you pass --run-summary)
    if args.run_summary:
        from .extraction.dashboard_run_news_summarization import run_all_summaries
        run_all_summaries(db)
        logger.info("[summary] LLM summarization complete.")

    # STEP 7: Risk Fundamentals (Runs only if you pass --run-risk-fundamentals)
    if args.run_risk_fundamentals:
        from .extraction.run_risk_fundamentals import run_risk_fundamental_extraction
        result = run_risk_fundamental_extraction(db)
        logger.info("[risk_fundamentals] %s", result)

if __name__ == "__main__":
    main()