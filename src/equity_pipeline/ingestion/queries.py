"""
Named read helpers over the CouchDB client's generic find(). Nothing here
writes anything — this is the "read from db" half that was missing.

Mango queries need indexes to run efficiently at scale; create_indexes()
should be called once (e.g. right after ensure_db()) before these are used
on a non-trivial dataset.
"""


def create_indexes(db):
    """The `find` queries are served by the GIN index on the whole document (PostgresClient.ensure_db).
    These two help the hand-written SQL that filters on type, ticker and the extraction status."""
    # Earlier versions made three indexes that no query could use (wrong expressions, or a leftover
    # of the Google News fetcher). Dropped here so existing databases are cleaned up on the next run.
    for old in ("extraction_status_idx", "metric_match_idx", "google_link_idx"):
        db.drop_index(old)
    db.create_index(["type", "ticker"], name="type_ticker_idx")
    db.create_index(["type", "extraction.status"], name="type_extraction_status_idx")


def get_articles_for_ticker(db, ticker: str, limit: int = 200):
    return db.find({"type": "raw_article", "ticker": ticker}, limit=limit)


def get_filings_for_ticker(db, ticker: str, limit: int = 200):
    return db.find({"type": "sec_filing", "ticker": ticker}, limit=limit)


def get_pending_extractions(db, limit: int = 200):
    return db.find({"type": "raw_article", "extraction.status": "pending"}, limit=limit)


def get_articles_for_metric(db, ticker: str, metric_id: str, limit: int = 200):
    """Articles for one company that matched a specific metric_id from
    metrics.yaml (e.g. 'm05_gross_margin')."""
    return db.find(
        {
            "type": "raw_article",
            "ticker": ticker,
            "matched_metrics": {"$elemMatch": {"metric_id": metric_id}},
        },
        limit=limit,
    )


def get_unscored_articles(db, ticker: str = None, limit: int = 200):
    """Articles whose body_text is ready but sentiment hasn't been scored
    yet — the hand-off point for the future sentiment-scoring script that
    upsert.py's docstring describes."""
    selector = {"type": "raw_article", "extraction.status": "success", "sentiment": None}
    if ticker:
        selector["ticker"] = ticker
    return db.find(selector, limit=limit)



