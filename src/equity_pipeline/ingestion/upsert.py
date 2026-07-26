"""
Create-or-merge logic for raw_article and sec_filing docs (schema v2).

Sentiment is intentionally never touched here — every doc is created with
"sentiment": None and this module never writes to that field again. A future
sentiment-scoring script owns that field exclusively, via the same
db.upsert() pattern but its own update_fn that only patches "sentiment".
"""
from .dedup import article_doc_id, filing_doc_id
from .time_utils import now_iso, to_iso


def upsert_article(db, ticker, source, metric_scope, title, url, published_at, matched_metrics):
    """
    matched_metrics: list of {"metric_id": ..., "query_window": ...} — empty list
    for general-scope sources (yahoo_finance). For google_news hits, if the
    article already exists (found via an earlier metric query, or on a prior
    run), new metric_ids are appended rather than overwriting anything.
    """
    doc_id = article_doc_id(ticker, url)

    def update_fn(existing):
        now = now_iso()

        if existing is None:
            return {
                "type": "raw_article",
                "ticker": ticker,
                "source": source,
                "metric_scope": metric_scope,
                "title": title,
                "url": url,
                "published_at": to_iso(published_at),
                "first_fetched_at": now,
                "last_seen_at": now,
                "matched_metrics": matched_metrics,
                "extraction": {
                    "status": "pending",
                    "fetch_method": None,
                    "fetched_at": None,
                    "body_char_count": None,
                },
                "body_text": None,
                "llm_summary": None,
                "relevance": None,
                "dedup": {"is_primary": True, "duplicate_of": None, "syndication_group_id": None},
                "sentiment": None,
            }

        # Existing doc: bump last_seen_at, append any new metric matches,
        # and otherwise leave everything (especially body_text/sentiment) alone.
        existing["last_seen_at"] = now
        existing_ids = {m["metric_id"] for m in existing.get("matched_metrics", [])}
        for m in matched_metrics:
            if m["metric_id"] not in existing_ids:
                existing["matched_metrics"].append(m)
                existing_ids.add(m["metric_id"])
        return existing

    db.upsert(doc_id, update_fn)
    return doc_id


def upsert_filing(db, ticker, filing: dict):
    """
    filing: {"form_type", "accession", "section", "filing_date", "url",
             "source_filename", "body_text"}
    SEC filings are fetched and cleaned synchronously (unlike news articles),
    so body_text is already final at write time — no separate extraction
    phase needed for this doc type.
    """
    doc_id = filing_doc_id(ticker, filing["accession"], filing["section"])

    def update_fn(existing):
        if existing and existing.get("body_text"):
            return existing  # filings are immutable once captured; don't re-fetch

        return {
            "type": "sec_filing",
            "ticker": ticker,
            "metric_scope": "general",
            "form_type": filing["form_type"],
            "accession": filing["accession"],
            "section": filing["section"],
            "filing_date": filing.get("filing_date"),
            "source_url": filing["url"],
            "source_filename": filing.get("source_filename"),
            "body_text": filing.get("body_text"),
            "fetched_at": now_iso(),
            "extraction": {
                "status": "success" if filing.get("body_text") else "failed",
                "fetch_method": "sec_edgar_direct",
            },
            "sentiment": None,
        }

    db.upsert(doc_id, update_fn)
    return doc_id


def apply_extraction_result(db, doc_id: str, status: str, fetch_method: str, body_text: str | None):
    """Called by the batch extractor once body text has been fetched for a
    raw_article doc that started life with extraction.status == 'pending'."""

    def update_fn(existing):
        if existing is None:
            raise RuntimeError(f"{doc_id} was deleted before its extraction result could be applied")
        existing["extraction"] = {
            "status": status,
            "fetch_method": fetch_method,
            "fetched_at": now_iso(),
            "body_char_count": len(body_text) if body_text else 0,
        }
        existing["body_text"] = body_text
        return existing

    db.upsert(doc_id, update_fn)
