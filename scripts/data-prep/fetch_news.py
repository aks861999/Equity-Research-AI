"""
Fetch the per-metric news for every company ahead of time, through the local SearXNG container,
and store it in Postgres (Yahoo Finance and SEC EDGAR are fetched by the normal ingestion).

    python scripts/data-prep/fetch_news.py                       # every company in companies.yaml
    python scripts/data-prep/fetch_news.py MELI KO               # only these
    python scripts/data-prep/fetch_news.py --max-per-metric 5    # fewer articles per metric (default 10)
    python scripts/data-prep/fetch_news.py --extract             # then download the article texts too
    python scripts/data-prep/fetch_news.py --report              # only print date coverage, no searching

Needs the SearXNG container running (SEARXNG_URL in .env, default http://localhost:8888) and the
Postgres container. Each of the 25 growth metrics is searched as "<company> <key phrase>", with a
second phrase only if the first found too few articles: 25-50 queries per company, spaced 8-14 s
apart (about 5-9 minutes per company). A query already answered completely today is read from
data/news_queries/ instead. Articles already stored are not
duplicated and keep their text and sentiment.

Up to 10 articles per metric are kept and nothing is dropped for its keywords; how much each
article is really about the metric is judged on its full text in the aggregation
(extraction/metric_relevance.py), so an off-topic hit only counts with weight 0.2.

After --extract, articles without a publish date get one from their page where the page has it.
The report shows how many dates came from the source, the page, or are still unknown (weighted as
UNKNOWN_DATE_AGE_DAYS old in the aggregation).
"""
import argparse
import asyncio
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from equity_pipeline.config import get_company, load_companies, load_metrics  # noqa: E402
from equity_pipeline.main import get_db, ingest_searxng_news  # noqa: E402

logger = logging.getLogger("data-prep")


def date_report(db, tickers: list[str]) -> None:
    print("\nDATE COVERAGE (news articles in the database)")
    print(f"  {'ticker':8} {'source':>8} {'page':>6} {'unknown':>8} {'total':>6}")
    for ticker in tickers:
        docs = db.find({"type": "raw_article", "ticker": ticker, "source": "searxng_news"},
                       limit=100000, fields=["date_source"])
        counts = Counter(d.get("date_source") or "unknown" for d in docs)
        print(f"  {ticker:8} {counts['source']:>8} {counts['page']:>6} {counts['unknown']:>8} {len(docs):>6}")


def quality_report(db, tickers: list[str]) -> None:
    """Texts by outcome of the extraction and the quality gate."""
    print("\nQUALITY (what happened to each article's text)")
    print(f"  {'ticker':8} {'usable':>7} {'rejected':>9} {'failed':>7} {'pending':>8} {'browser':>8}   rejected by reason")
    for ticker in tickers:
        docs = db.find({"type": "raw_article", "ticker": ticker}, limit=100000, fields=["extraction"])
        status = Counter((d.get("extraction") or {}).get("status") for d in docs)
        reasons = Counter((d.get("extraction") or {}).get("reject_reason") for d in docs
                          if (d.get("extraction") or {}).get("status") == "rejected")
        why = ", ".join(f"{r} {n}" for r, n in reasons.most_common()) or "-"
        print(f"  {ticker:8} {status['success']:>7} {status['rejected']:>9} {status['failed']:>7} "
              f"{status['pending']:>8} {status['needs_browser']:>8}   {why}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch per-metric news through SearXNG.")
    parser.add_argument("tickers", nargs="*", help="default: every ticker in config/companies.yaml")
    parser.add_argument("--max-per-metric", type=int, default=10,
                        help="articles kept per metric (default 10: some pages fail extraction)")
    parser.add_argument("--no-cache", action="store_true", help="ask SearXNG again even for queries answered today")
    parser.add_argument("--include-risk", action="store_true",
                        help="also search the 11 risk metrics (the agent does not read their sentiment)")
    parser.add_argument("--extract", action="store_true", help="download article texts afterwards")
    parser.add_argument("--report", action="store_true", help="only print the date coverage")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers] or [c["ticker"] for c in load_companies()]
    db = get_db()

    if not args.report:
        metrics = load_metrics()
        for ticker in tickers:
            company = get_company(ticker)
            logger.info("=== %s (%s) ===", ticker, company["primary_search_name"])
            ingest_searxng_news(db, company, metrics, max_per_metric=args.max_per_metric,
                                use_cache=not args.no_cache, include_risk=args.include_risk)

        if args.extract:
            from equity_pipeline.extraction.run_extraction import run_pending_extractions
            logger.info("[extraction] %s", asyncio.run(run_pending_extractions(db, batch_limit=100000)))

    date_report(db, tickers)
    quality_report(db, tickers)


if __name__ == "__main__":
    main()
