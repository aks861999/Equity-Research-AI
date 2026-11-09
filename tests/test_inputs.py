"""Drift checks: the declared inputs (sources/inputs.py) must match what the calculators read.

Run from the project root:   python scripts/data-prep/check_inputs.py
or with pytest:              python -m pytest tests/test_inputs.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from equity_pipeline.extraction.run_growth_fundamentals import compute_growth_metrics  # noqa: E402
from equity_pipeline.extraction.run_risk_fundamentals import compute_risk_metrics  # noqa: E402
from equity_pipeline.sources.inputs import CALCULATOR_INPUTS, FALLBACK_TAGS, INPUTS, METRIC_INPUTS  # noqa: E402


class RecordingStatements:
    """Stands in for CompanyStatements and records every (field, offset) a calculator reads."""
    years = [2025, 2024, 2023]

    def __init__(self):
        self.reads: set[tuple[str, int]] = set()

    def get(self, offset: int, field: str) -> float:
        self.reads.add((field, offset))
        return 100.0 + offset


def _fields_read() -> set[str]:
    stm = RecordingStatements()
    compute_growth_metrics(stm)
    compute_risk_metrics(stm)
    return {field for field, _ in stm.reads}


def test_every_field_the_calculators_read_is_declared():
    undeclared = _fields_read() - set(INPUTS)
    assert not undeclared, f"declare these in sources/inputs.py first: {sorted(undeclared)}"


def test_metric_inputs_match_what_the_calculators_read():
    declared_by_metrics = {field for pairs in METRIC_INPUTS.values() for field, _ in pairs}
    read = _fields_read()
    assert read == declared_by_metrics, (
        f"read but not in METRIC_INPUTS: {sorted(read - declared_by_metrics)}; "
        f"in METRIC_INPUTS but never read: {sorted(declared_by_metrics - read)}")


def test_every_calculator_input_is_used_by_a_metric():
    used = {field for pairs in METRIC_INPUTS.values() for field, _ in pairs}
    assert CALCULATOR_INPUTS <= used, f"declared but unused: {sorted(CALCULATOR_INPUTS - used)}"


def test_metric_ids_match_the_agent_definitions():
    from equity_pipeline.agent.metric_definitions import GROWTH_METRIC_DEFINITIONS, RISK_METRIC_DEFINITIONS
    assert set(METRIC_INPUTS) == set(GROWTH_METRIC_DEFINITIONS) | set(RISK_METRIC_DEFINITIONS)


def test_fallback_tags_come_from_the_declared_inputs():
    assert set(FALLBACK_TAGS) <= set(INPUTS)
    assert all(INPUTS[f].kind != "derived" for f in FALLBACK_TAGS)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
