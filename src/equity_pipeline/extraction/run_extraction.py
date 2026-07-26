"""
Pulls every raw_article doc with extraction.status == "pending" and runs it
through article_extractor.fetch_and_extract(), writing the result back via
ingestion.upsert.apply_extraction_result(). This is the piece that was
missing entirely before: without it, articles sit in CouchDB forever with
body_text == None.
"""
import asyncio
import logging

from ..ingestion.upsert import apply_extraction_result
from .article_extractor import ExtractorConfig, fetch_and_extract

logger = logging.getLogger("run_extraction")


def find_pending_articles(db, limit: int = 200):
    return db.find({"type": "raw_article", "extraction.status": "pending"}, limit=limit)


async def run_pending_extractions(db, batch_limit: int = 200, delay_seconds: float = 1.0):
    """Fetch+extract every currently-pending article, one at a time (politely
    delayed) so as not to hammer publisher sites. Returns counts for logging."""
    pending = find_pending_articles(db, limit=batch_limit)
    config = ExtractorConfig()
    done, failed = 0, 0

    for doc in pending:
        url = doc["url"]
        status, method, body_text = await fetch_and_extract(url, config)
        apply_extraction_result(db, doc["_id"], status, method, body_text)
        if status == "success":
            done += 1
        else:
            failed += 1
        logger.info("[extraction] %s -> %s (%s)", url, status, method)
        await asyncio.sleep(delay_seconds)

    return {"processed": len(pending), "succeeded": done, "failed": failed}
