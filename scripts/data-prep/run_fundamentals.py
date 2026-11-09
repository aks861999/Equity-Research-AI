"""
Calculate the fundamental ratios for every company in config/companies.yaml and store them
(with Red / Yellow / Green grades) in the `fundamental_metrics` table of Postgres.

    python scripts/data-prep/run_fundamentals.py              # 25 growth + 11 risk ratios
    python scripts/data-prep/run_fundamentals.py --growth     # growth ratios only
    python scripts/data-prep/run_fundamentals.py --risk       # risk ratios only

Same result as `python -m equity_pipeline.main --run-fundamentals --run-risk-fundamentals`,
without needing PYTHONPATH. Needs the Postgres container running and SEC_USER_AGENT in .env.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from equity_pipeline.main import get_db  # noqa: E402
from equity_pipeline.extraction.run_growth_fundamentals import run_fundamental_extraction  # noqa: E402
from equity_pipeline.extraction.run_risk_fundamentals import run_risk_fundamental_extraction  # noqa: E402

logger = logging.getLogger("data-prep")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate and store the fundamental ratios.")
    parser.add_argument("--growth", action="store_true", help="only the 25 growth ratios")
    parser.add_argument("--risk", action="store_true", help="only the 11 risk ratios")
    args = parser.parse_args()
    run_growth = args.growth or not args.risk
    run_risk = args.risk or not args.growth

    db = get_db()
    if run_growth:
        logger.info("[fundamentals] %s", run_fundamental_extraction(db))
    if run_risk:
        logger.info("[risk_fundamentals] %s", run_risk_fundamental_extraction(db))


if __name__ == "__main__":
    main()
