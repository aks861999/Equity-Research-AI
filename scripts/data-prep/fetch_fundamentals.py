"""
Fetch and cache SEC fundamentals for one or more tickers (data/sec/<TICKER>.json).

    python scripts/data-prep/fetch_fundamentals.py MELI AAPL
    python scripts/data-prep/fetch_fundamentals.py MELI --refresh     # ignore the 7-day cache

Needs SEC_USER_AGENT in .env. The fetching logic lives in
src/equity_pipeline/sources/sec_fundamentals.py; this file is only the entry point.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from equity_pipeline.sources.sec_fundamentals import main  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
