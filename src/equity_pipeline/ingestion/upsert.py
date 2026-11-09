"""
Create-or-merge logic for raw_article and sec_filing docs (schema v2).

Sentiment is intentionally never touched here — every doc is created with
"sentiment": None and this module never writes to that field again. A future
sentiment-scoring script owns that field exclusively, via the same
db.upsert() pattern but its own update_fn that only patches "sentiment".
"""
from .dedup import article_doc_id, filing_doc_id, title_key
from .time_utils import now_iso, to_iso


def upsert_article(db, ticker, source, metric_scope, title, url, published_at, matched_metrics,
                   google_link=None, date_source=None):
    """
    matched_metrics: list of {"metric_id": ..., "query_window": ...} — empty list
    for general-scope sources (yahoo_finance). For per-metric news hits, if the
    article already exists (found via an earlier metric query, or on a prior
    run), new metric_ids are appended rather than overwriting anything.

    date_source records where published_at came from: "source" (the feed or search
    engine gave it), "page" (read from the article page during extraction, see
    apply_extraction_result) or "unknown" (no date anywhere yet; aggregation then
    assumes UNKNOWN_DATE_AGE_DAYS).
    """
    doc_id = article_doc_id(ticker, url)
    date_source = date_source or ("source" if published_at else "unknown")

    def update_fn(existing):
        now = now_iso()

        if existing is None:
            return {
                "type": "raw_article",
                "ticker": ticker,
                "source": source,
                "metric_scope": metric_scope,
                "title": title,
                "title_key": title_key(title),
                "url": url,
                "google_link": google_link,
                "published_at": to_iso(published_at),
                "date_source": date_source,
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
        existing.setdefault("title_key", title_key(existing.get("title")))
        if existing.get("published_at") is None and published_at:
            existing["published_at"] = to_iso(published_at)
            existing["date_source"] = date_source
        existing_ids = {m["metric_id"] for m in existing.get("matched_metrics", [])}
        for m in matched_metrics:
            if m["metric_id"] not in existing_ids:
                existing["matched_metrics"].append(m)
                existing_ids.add(m["metric_id"])
        return existing

    db.upsert(doc_id, update_fn)
    return doc_id


def store_metric_news(db, ticker: str, articles: list[dict], source: str = "searxng_news") -> int:
    """Stores the per-metric news articles of one company (output of
    sources.searxng_news.collect_all_metric_articles).

    Syndicated copies across runs: if an article with the same normalised title is already stored
    for this ticker under another URL, the new hit is merged into that stored article (its metric
    tags are appended) instead of creating a second copy of the same story."""
    for article in articles:
        url = article["url"]
        key = title_key(article["title"])
        if key:
            same_story = db.find({"type": "raw_article", "ticker": ticker, "title_key": key},
                                 limit=1, fields=["url"])
            if same_story and same_story[0].get("url"):
                url = same_story[0]["url"]
        upsert_article(
            db, ticker, source=source, metric_scope="metric",
            title=article["title"], url=url,
            published_at=article["published_at"], matched_metrics=article["matched_metrics"],
        )
    return len(articles)


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


PAGE_DATE_OVERRIDE_DAYS = 2  # a page date this far from the source's date replaces it (flagged)


def _days_apart(iso_a: str, iso_b: str) -> float | None:
    from datetime import datetime, timezone
    try:
        a, b = (datetime.fromisoformat(x.replace("Z", "+00:00")) for x in (iso_a, iso_b))
    except ValueError:
        return None
    a, b = (x if x.tzinfo else x.replace(tzinfo=timezone.utc) for x in (a, b))
    return abs((a - b).total_seconds()) / 86400


def apply_extraction_result(db, doc_id: str, status: str, fetch_method: str, body_text: str | None,
                            published_at=None, reject_reason: str | None = None):
    """Called by the batch extractor once body text has been fetched for a
    raw_article doc that started life with extraction.status == 'pending'.

    published_at: the publish date read from the article page, if one was found.
    It fills a missing date (date_source = "page"). If the source already gave a date and the
    page date differs by more than PAGE_DATE_OVERRIDE_DAYS, the page date wins and the old
    one is kept in engine_published_at so the disagreement can be audited."""

    def update_fn(existing):
        if existing is None:
            raise RuntimeError(f"{doc_id} was deleted before its extraction result could be applied")
        existing["extraction"] = {
            "status": status,
            "fetch_method": fetch_method,
            "fetched_at": now_iso(),
            "body_char_count": len(body_text) if body_text else 0,
        }
        if reject_reason:
            # status "rejected": the quality gate (extraction/quality_gate.py) found the text unusable.
            # No text is stored and it is never scored; this marker row stops it being downloaded again.
            existing["extraction"]["reject_reason"] = reject_reason
        existing["body_text"] = body_text
        if published_at:
            page_iso = to_iso(published_at)
            old = existing.get("published_at")
            if old is None:
                existing["published_at"] = page_iso
                existing["date_source"] = "page"
            else:
                apart = _days_apart(old, page_iso)
                if apart is not None and apart > PAGE_DATE_OVERRIDE_DAYS:
                    existing["engine_published_at"] = old
                    existing["published_at"] = page_iso
                    existing["date_source"] = "page"
        return existing

    db.upsert(doc_id, update_fn)



def apply_sentiment_result(db, doc_id: str, sentiment_payload: dict):
    """Called by the sentiment runner to safely update only the sentiment field."""
    
    def update_fn(existing):
        if existing is None:
            raise RuntimeError(f"{doc_id} was deleted before its sentiment could be applied")
        existing["sentiment"] = sentiment_payload
        return existing

    db.upsert(doc_id, update_fn)