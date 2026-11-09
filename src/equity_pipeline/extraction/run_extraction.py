"""
Pulls every raw_article doc with extraction.status == "pending" and runs it
through article_extractor.fetch_and_extract(), writing the result back via
ingestion.upsert.apply_extraction_result(). This is the piece that was
missing entirely before: without it, articles sit in CouchDB forever with
body_text == None.
"""
import asyncio
import logging

from ..config import get_company
from ..ingestion.upsert import apply_extraction_result
from .article_extractor import ExtractorConfig, fetch_and_extract_with_date
from .quality_gate import assess

logger = logging.getLogger("run_extraction")


def _company_of(doc: dict) -> tuple[str | None, str | None]:
    """(company name, ticker) of an article, for the quality gate's company check."""
    ticker = doc.get("ticker")
    try:
        return get_company(ticker)["primary_search_name"], ticker
    except (ValueError, KeyError):  # a ticker no longer in companies.yaml: skip the company check
        return None, ticker


def find_pending_articles(db, limit: int = 200, ticker: str | None = None, source: str | None = None,
                          include_needs_browser: bool | None = None):
    """Articles whose text still has to be downloaded. Optionally only one ticker / one source.

    `needs_browser` articles (pages curl_cffi could not read, set aside by the online demo) are picked up
    again whenever the browser fallback is allowed, i.e. by your backend; the demo never retries them."""
    if include_needs_browser is None:
        include_needs_browser = ExtractorConfig().allow_browser_fallback
    statuses = ["pending"] + (["needs_browser"] if include_needs_browser else [])
    found: list[dict] = []
    for status in statuses:
        selector = {"type": "raw_article", "extraction.status": status}
        if ticker:
            selector["ticker"] = ticker
        if source:
            selector["source"] = source
        found.extend(db.find(selector, limit=limit - len(found)))
        if len(found) >= limit:
            break
    return found


async def run_pending_extractions(db, batch_limit: int = 200, delay_seconds: float = 1.0,
                                  ticker: str | None = None, source: str | None = None):
    """Fetch+extract every currently-pending article, one at a time (politely
    delayed) so as not to hammer publisher sites. Returns counts for logging.
    `ticker` / `source` restrict the run (the live pipeline passes the selected ticker)."""
    pending = find_pending_articles(db, limit=batch_limit, ticker=ticker, source=source)
    config = ExtractorConfig()
    done, failed, skipped = 0, 0, 0
    needs_browser = 0
    rejected: dict[str, int] = {}

    for doc in pending:
        url = doc["url"]
        try:
            status, method, body_text, page_date = await fetch_and_extract_with_date(url, config)
        except Exception as exc:  # one broken page must never stop the batch; it stays pending
            logger.error("[extraction] %s raised %s: %s", url, type(exc).__name__, exc)
            skipped += 1
            continue
        if status == "skipped":
            # needs the browser fallback and the browser cannot start: leave it pending
            skipped += 1
            logger.info("[extraction] %s -> skipped, stays pending (%s)", url, method)
            continue
        if status == "needs_browser":
            # curl_cffi could not read the page and the browser fallback is not allowed here (online
            # demo): set it aside. Never retried online; the backend reads it with the browser.
            apply_extraction_result(db, doc["_id"], "needs_browser", method, None, published_at=page_date)
            needs_browser += 1
            logger.info("[extraction] %s -> needs_browser (left for the backend)", url)
            await asyncio.sleep(delay_seconds)
            continue
        reason = None
        if status == "success":
            reason = assess(doc.get("title"), body_text, *_company_of(doc))
        if reason:
            apply_extraction_result(db, doc["_id"], "rejected", method, None, reject_reason=reason)
            rejected[reason] = rejected.get(reason, 0) + 1
            logger.info("[extraction] %s -> rejected (%s)", url, reason)
        else:
            apply_extraction_result(db, doc["_id"], status, method, body_text, published_at=page_date)
            if status == "success":
                done += 1
            else:
                failed += 1
            logger.info("[extraction] %s -> %s (%s)", url, status, method)
        await asyncio.sleep(delay_seconds)

    return {"processed": len(pending), "succeeded": done, "failed": failed,
            "rejected": sum(rejected.values()), "rejected_by_reason": rejected,
            "needs_browser": needs_browser, "skipped_still_pending": skipped}
