"""
Report which fundamentals a company would get, before adding it to config/companies.yaml.
No database is touched.

    python scripts/data-prep/coverage_report.py NEWTICKER OTHER
    python scripts/data-prep/coverage_report.py                    # every ticker in companies.yaml
    python scripts/data-prep/coverage_report.py NEWTICKER --refresh

The report logic lives in src/equity_pipeline/sources/coverage.py; this file is only the entry point.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from equity_pipeline.sources.coverage import main  # noqa: E402

if __name__ == "__main__":
    main()
