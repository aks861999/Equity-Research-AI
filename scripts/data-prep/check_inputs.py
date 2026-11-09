"""
Check that the declared inputs (src/equity_pipeline/sources/inputs.py) still match what the
ratio calculators read. Run it after changing a runner or adding a metric.

    python scripts/data-prep/check_inputs.py

The checks live in tests/test_inputs.py (pytest also runs them); this file is only the entry point.
"""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    runpy.run_path(str(ROOT / "tests" / "test_inputs.py"), run_name="__main__")
