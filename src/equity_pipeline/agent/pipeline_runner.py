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

Deliberate deviation from main.py: main.py's ingest_company() only runs the
per-metric news search with --with-news, because it is the slowest step. The
growth question depends on metric_scores (which only exist for articles matched
to a metric by that search), so this runner can run it too. If you'd rather
keep it off (faster, but growth-question sentiment stays whatever was last
computed), set `include_news=False`. The news comes from the local SearXNG
container (sources/searxng_news.py); queries answered earlier today are cached.

Performance note: SEC EDGAR and the news search both have deliberate
rate-limit sleeps (see sources/sec_edgar.py REQUEST_DELAY,
sources/searxng_news.py QUERY_DELAY_SECONDS), and FinBERT scoring is a local model call per
article. A single ticker refresh can realistically take anywhere from
~15 seconds (little new data) to a couple of minutes (many new filings/
articles). Expose this as a toggle in any PM-facing UI rather than forcing
it on every click.
"""
from __future__ import annotations

import asyncio
import logging

from ..config import app_mode, get_company, load_metrics
from ..extraction.run_aggregation import run_metric_aggregation
from ..extraction.run_extraction import find_pending_articles, run_pending_extractions
from ..extraction.run_growth_fundamentals import run_fundamental_extraction
from ..extraction.run_risk_fundamentals import run_risk_fundamental_extraction
# run_sentiment (FinBERT, torch) is imported inside run_live_pipeline, only in full mode: the online
# demo install has neither torch nor transformers.
from ..ingestion.dedup import article_doc_id
from ..ingestion.upsert import store_metric_news, upsert_article, upsert_filing
from ..sources.searxng_news import collect_all_metric_articles, news_metrics
from ..sources.sec_edgar import fetch_sec_filings
from ..sources.yahoo_finance import fetch_yahoo_news

logger = logging.getLogger("agent.pipeline_runner")


def _ingest_sec(db, company: dict, check: dict | None = None) -> int:
    """New filing sections stored. `check` is filled with what the live fetch found (see
    sec_edgar.fetch_sec_filings): feed_ok, filings_checked, sections_new, sections_stored, or
    skipped (no form type configured) / error."""
    check = check if check is not None else {}
    if not company.get("sec_form_type"):
        check["skipped"] = "no sec_form_type configured"
        return 0
    try:
        filings = fetch_sec_filings(company["ticker"], company["sec_form_type"], db=db, stats=check)
    except Exception as exc:  # a failed SEC call must not stop the run; the brief uses what is stored
        logger.warning("SEC EDGAR fetch failed for %s: %s", company["ticker"], exc)
        check.update({"feed_ok": False, "error": str(exc)})
        return 0
    for filing in filings:
        upsert_filing(db, company["ticker"], filing)
    return len(filings)


def _ingest_yahoo(db, company: dict, check: dict | None = None) -> int:
    """New Yahoo articles stored. `check` is filled with ok / fetched / new (see live_data_notices)."""
    check = check if check is not None else {}
    articles = fetch_yahoo_news(company["ticker"], stats=check)
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
    check["new"] = upserted
    return upserted


def live_data_notices(sec_check: dict, yahoo_check: dict) -> list[dict]:
    """What the live fetch found, as messages for the dashboard: [{"level": info|success|warning, "text": ...}].

    Three outcomes per source, which the plain counts (0 new) cannot tell apart:
      * the source answered and everything it returned is already stored -> "nothing new"
      * the source answered and some items are new                        -> "N new stored"
      * the source could not be reached                                    -> a warning: the brief
        uses what is already stored"""
    notices: list[dict] = []

    if sec_check.get("skipped"):
        notices.append({"level": "info", "text": f"SEC EDGAR: skipped ({sec_check['skipped']})."})
    elif not sec_check.get("feed_ok", True):
        notices.append({"level": "warning", "text": "SEC EDGAR could not be reached just now. The brief uses the "
                                                    "filings already stored, which may be out of date."})
    else:
        new, stored = sec_check.get("sections_new", 0), sec_check.get("sections_stored", 0)
        checked = sec_check.get("filings_checked", 0)
        if new:
            notices.append({"level": "success", "text": f"SEC EDGAR: {new} new filing section(s) stored "
                                                        f"({stored} already stored, {checked} recent filings checked)."})
        elif checked:
            notices.append({"level": "info", "text": f"SEC EDGAR: checked the live feed ({checked} recent filings, "
                                                     f"{stored} sections). Everything is already stored: nothing "
                                                     f"new since the last refresh."})
        else:
            notices.append({"level": "info", "text": "SEC EDGAR: the live feed lists no recent filings for this "
                                                     "company."})

    if not yahoo_check.get("ok", True):
        notices.append({"level": "warning", "text": "Yahoo Finance could not be reached just now. The brief uses "
                                                    "the headlines already stored, which may be out of date."})
    else:
        fetched, new = yahoo_check.get("fetched", 0), yahoo_check.get("new", 0)
        if new:
            notices.append({"level": "success", "text": f"Yahoo Finance: {new} new headline(s) stored "
                                                        f"({fetched} fetched)."})
        elif fetched:
            notices.append({"level": "info", "text": f"Yahoo Finance: fetched {fetched} headlines. Everything is "
                                                     f"already stored: nothing new since the last refresh."})
        else:
            notices.append({"level": "info", "text": "Yahoo Finance returned no headlines for this company."})
    return notices


def _ingest_searxng_news(db, company: dict, metrics: list[dict]) -> int:
    articles = collect_all_metric_articles(company["primary_search_name"], news_metrics(metrics))
    return store_metric_news(db, company["ticker"], articles)

def run_live_pipeline(db, ticker: str, include_news: bool = True, progress_callback=None,
                      mode: str | None = None) -> dict:
    """Refresh the data behind one ticker, then the agent reads it.

    mode 'full' (your PC): SEC filings, Yahoo headlines, optional SearXNG news search, text extraction
    (curl_cffi, browser fallback allowed), FinBERT scoring, aggregation and the ratios, all for the
    selected ticker (the aggregation reads every scored article, as before).

    mode 'demo' (online): ONLY the live SEC EDGAR and Yahoo Finance parts: SEC filings, Yahoo headlines and
    the text of the new Yahoo articles read with curl_cffi alone (a page that needs the browser is set
    aside as needs_browser for the backend). No SearXNG, no FinBERT, no aggregation, no ratio
    recomputation: those are read from the shared database, prepared by the full-mode backend."""
    def _p(msg: str) -> None:
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    mode = mode or app_mode()
    company = get_company(ticker)
    counts: dict = {"ticker": ticker, "mode": mode}

    sec_check: dict = {}
    yahoo_check: dict = {}

    _p(f"[live_pipeline] {ticker}: SEC EDGAR ingest...")
    counts["sec_filings_ingested"] = _ingest_sec(db, company, sec_check)

    _p(f"[live_pipeline] {ticker}: Yahoo Finance ingest...")
    counts["yahoo_articles_ingested"] = _ingest_yahoo(db, company, yahoo_check)

    # What the fetch found (new / nothing new / unreachable), for the dashboard and the CLI.
    counts["live_check"] = {"sec": sec_check, "yahoo": yahoo_check}
    counts["notices"] = live_data_notices(sec_check, yahoo_check)

    if mode == "demo":
        waiting = len(find_pending_articles(db, limit=50, ticker=ticker, source="yahoo_finance"))
        _p(f"[live_pipeline] {ticker}: reading the text of {waiting} new Yahoo article(s) (curl_cffi only)..."
           if waiting else f"[live_pipeline] {ticker}: no new Yahoo articles to read.")
        counts["extraction"] = asyncio.run(
            run_pending_extractions(db, batch_limit=50, ticker=ticker, source="yahoo_finance"))
        for step in ("news_articles_ingested", "sentiment", "aggregation", "growth_fundamentals", "risk_fundamentals"):
            counts[step] = "skipped (demo: read from the shared database)"
        _p(f"[live_pipeline] {ticker} complete (demo).")
        return counts

    if include_news:
        _p(f"[live_pipeline] {ticker}: per-metric news search (SearXNG)...")
        counts["news_articles_ingested"] = _ingest_searxng_news(db, company, load_metrics())
    else:
        counts["news_articles_ingested"] = "skipped"

    waiting = len(find_pending_articles(db, limit=200, ticker=ticker))
    _p(f"[live_pipeline] {ticker}: reading the text of {waiting} new article(s)..."
       if waiting else f"[live_pipeline] {ticker}: no new articles to read.")
    counts["extraction"] = asyncio.run(run_pending_extractions(db, batch_limit=200, ticker=ticker))

    _p(f"[live_pipeline] {ticker}: FinBERT sentiment scoring...")
    from ..extraction.run_sentiment import run_pending_sentiments  # torch: full mode only
    counts["sentiment"] = run_pending_sentiments(db, batch_limit=2000, ticker=ticker)

    _p(f"[live_pipeline] {ticker}: metric-score aggregation...")
    counts["aggregation"] = run_metric_aggregation(db)

    _p(f"[live_pipeline] {ticker}: growth fundamentals (SEC)...")
    counts["growth_fundamentals"] = run_fundamental_extraction(db, tickers=[ticker])

    _p(f"[live_pipeline] {ticker}: risk fundamentals (SEC)...")
    counts["risk_fundamentals"] = run_risk_fundamental_extraction(db, tickers=[ticker])

    _p(f"[live_pipeline] {ticker} complete.")
    return counts