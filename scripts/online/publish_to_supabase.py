"""
Publish the prepared data from your local database to the hosted one (Supabase).

    python scripts/online/publish_to_supabase.py --dry-run            # count what would be copied, write nothing
    python scripts/online/publish_to_supabase.py                      # publish every company
    python scripts/online/publish_to_supabase.py AAPL KO              # only these
    python scripts/online/publish_to_supabase.py --keep-search-text   # also publish the text of search-news articles

Source: the database in .env (POSTGRES_*), your PC. Target: TARGET_DATABASE_URL in .env, or --target-url.
Create the tables there first: python scripts/online/apply_schema.py --target-url "<url>" --rls

What is copied (one way, local to hosted; rows are replaced by id, nothing on the target is deleted):

  fundamental_metrics   all rows (the 36 ratios, with fiscal year end and currency)
  metric_scores         all rows (the per-metric news sentiment)
  articles              all documents, with ONE exception: the body text of search-news articles is left out
                        (`searxng_news`). The online app never reads it (it reads their scores and links), and
                        leaving it out keeps the hosted database small and third-party text off the public host.
                        Yahoo headlines and SEC filing sections keep their text (the agent's lead and excerpts).

Run it after `--run-sentiment` and `--run-aggregation`, so the hosted copy carries the scores.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import psycopg2  # noqa: E402
from psycopg2.extras import Json, execute_values  # noqa: E402

from equity_pipeline.config import db_settings, load_companies, target_database_url  # noqa: E402

BATCH = 200


def connect_source():
    s = db_settings()
    options = dict(host=s["host"], port=s["port"], dbname=s["db_name"], user=s["user"], password=s["password"])
    if s.get("sslmode"):
        options["sslmode"] = s["sslmode"]
    return psycopg2.connect(**options)


def strip_search_text(doc: dict) -> dict:
    """The document as published: a search-news article without its text (status and counts stay)."""
    if doc.get("type") == "raw_article" and doc.get("source") == "searxng_news":
        doc = {**doc, "body_text": None}
    return doc


def copy_table(src, dst, label: str, select_sql: str, params, insert_sql: str, transform=None, dry_run=False) -> int:
    n = 0
    with src.cursor(name=f"copy_{label}") as read:      # a server-side cursor: no big result held in memory
        read.itersize = BATCH
        read.execute(select_sql, params)
        batch = []
        for row in read:
            batch.append(transform(row) if transform else row)
            if len(batch) >= BATCH:
                n += flush(dst, insert_sql, batch, dry_run)
                batch = []
        n += flush(dst, insert_sql, batch, dry_run)
    print(f"  {label:20} {n:>6} row(s){'  (dry run, nothing written)' if dry_run else ''}")
    return n


def flush(dst, insert_sql: str, batch: list, dry_run: bool) -> int:
    if not batch:
        return 0
    if not dry_run:
        with dst.cursor() as cur:
            execute_values(cur, insert_sql, batch)
        dst.commit()
    return len(batch)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish the prepared data to the hosted database.")
    parser.add_argument("tickers", nargs="*", help="default: every ticker in config/companies.yaml")
    parser.add_argument("--target-url", help="default: TARGET_DATABASE_URL")
    parser.add_argument("--keep-search-text", action="store_true", help="also publish the text of search-news articles")
    parser.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    args = parser.parse_args()

    url = args.target_url or target_database_url()
    if not url:
        sys.exit("Set TARGET_DATABASE_URL in .env (the Supabase connection string) or pass --target-url.")
    tickers = [t.upper() for t in args.tickers] or [c["ticker"] for c in load_companies()]

    src, dst = connect_source(), psycopg2.connect(url)
    print(f"Publishing {', '.join(tickers)} from the local database to the target" + (" (dry run)" if args.dry_run else ""))

    copy_table(
        src, dst, "fundamental_metrics",
        "SELECT id, ticker, metric_id, fundamental_value, fundamental_grade, calculated_at, fiscal_year_end, "
        "period_type, currency FROM fundamental_metrics WHERE ticker = ANY(%s)", (tickers,),
        "INSERT INTO fundamental_metrics (id, ticker, metric_id, fundamental_value, fundamental_grade, calculated_at, "
        "fiscal_year_end, period_type, currency) VALUES %s ON CONFLICT (id) DO UPDATE SET "
        "fundamental_value = EXCLUDED.fundamental_value, fundamental_grade = EXCLUDED.fundamental_grade, "
        "calculated_at = EXCLUDED.calculated_at, fiscal_year_end = EXCLUDED.fiscal_year_end, "
        "period_type = EXCLUDED.period_type, currency = EXCLUDED.currency", dry_run=args.dry_run)

    copy_table(
        src, dst, "metric_scores",
        "SELECT id, ticker, metric_id, weighted_sentiment, dispersion, volume, calculated_at "
        "FROM metric_scores WHERE ticker = ANY(%s)", (tickers,),
        "INSERT INTO metric_scores (id, ticker, metric_id, weighted_sentiment, dispersion, volume, calculated_at) "
        "VALUES %s ON CONFLICT (id) DO UPDATE SET weighted_sentiment = EXCLUDED.weighted_sentiment, "
        "dispersion = EXCLUDED.dispersion, volume = EXCLUDED.volume, calculated_at = EXCLUDED.calculated_at",
        dry_run=args.dry_run)

    def article_row(row):
        doc_id, doc = row
        return doc_id, Json(doc if args.keep_search_text else strip_search_text(doc))

    copy_table(
        src, dst, "articles",
        "SELECT id, doc FROM articles WHERE doc->>'ticker' = ANY(%s)", (tickers,),
        "INSERT INTO articles (id, doc) VALUES %s ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc",
        transform=article_row, dry_run=args.dry_run)

    src.close()
    dst.close()
    print("Done." if not args.dry_run else "Dry run finished: nothing was written.")


if __name__ == "__main__":
    main()
