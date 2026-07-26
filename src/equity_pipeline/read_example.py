"""
`python -m equity_pipeline.read_example --ticker MELI`

Demonstrates the read side: pull back what main.py just ingested. This is
the piece that was previously entirely missing — writes worked, but nothing
read structured data back out of CouchDB by ticker/metric/status.
"""
import argparse

from .config import couchdb_settings
from .db.couchdb_client import CouchDBClient
from .ingestion.queries import (
    get_articles_for_ticker,
    get_filings_for_ticker,
    get_pending_extractions,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    args = parser.parse_args()

    db = CouchDBClient(**couchdb_settings())

    articles = get_articles_for_ticker(db, args.ticker)
    filings = get_filings_for_ticker(db, args.ticker)
    pending = get_pending_extractions(db)

    print(f"\n{args.ticker}: {len(articles)} articles, {len(filings)} SEC filing sections, "
          f"{len(pending)} articles still pending extraction (all tickers)\n")

    for a in articles[:10]:
        print(f"- [{a['source']}] {a['title']}  ({a['url']})")


if __name__ == "__main__":
    main()
