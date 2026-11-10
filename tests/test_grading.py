"""Offline checks for the Red / Yellow / Green grading of the 25 growth ratios (design document, Part 10).
Run: python tests/test_grading.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from equity_pipeline.extraction.grading import (  # noqa: E402
    get_grade, grade_growth_metrics, growth_context,
)

NAN = float("nan")


class FakeStatements:
    """get(offset, field) like CompanyStatements: offset 0 = latest year."""

    def __init__(self, rows):
        self.rows = rows

    def get(self, offset, field):
        try:
            return float(self.rows[offset][field])
        except (IndexError, KeyError, TypeError):
            return NAN


def test_level_bands_follow_the_document_at_and_around_their_cutoffs():
    cases = [
        ("m01_revenue_growth", [(0.09, "Red"), (0.10, "Yellow"), (0.149, "Yellow"), (0.15, "Green")]),
        ("m08_ebitda_margin", [(0.07, "Red"), (0.08, "Yellow"), (0.15, "Green")]),
        ("m09_net_profit_margin", [(0.049, "Red"), (0.05, "Yellow"), (0.10, "Green")]),
        ("m12_roa", [(0.04, "Red"), (0.07, "Yellow"), (0.10, "Green")]),
        ("m14_asset_turnover", [(0.9, "Red"), (1.25, "Yellow"), (1.5, "Green")]),
        ("m19_debt_to_equity", [(2.1, "Red"), (1.5, "Yellow"), (1.0, "Green")]),            # lower is better
        ("m20_debt_to_assets", [(0.6, "Red"), (0.4, "Yellow"), (0.3, "Green")]),
        ("m21_net_debt_to_ebitda", [(4.5, "Red"), (3.0, "Yellow"), (2.0, "Green")]),
        ("m23_quick_ratio", [(0.4, "Red"), (0.9, "Yellow"), (1.0, "Green")]),
        ("m24_cash_ratio", [(0.1, "Red"), (0.3, "Yellow"), (0.5, "Green")]),
        ("m25_interest_coverage", [(1.0, "Red"), (2.0, "Yellow"), (3.5, "Green")]),
    ]
    for metric_id, expected in cases:
        for value, grade in expected:
            assert get_grade(metric_id, value) == grade, (metric_id, value, grade)


def test_current_ratio_is_a_band_where_only_a_shortage_is_red():
    # design document: Red below 1.0; Yellow 1.0-1.5 or above 2.5; Green 1.5-2.0 (2.0-2.5 is not stated: Yellow)
    assert [get_grade("m22_current_ratio", v) for v in (0.9, 1.2, 1.5, 2.0, 2.3, 3.0)] == \
        ["Red", "Yellow", "Green", "Green", "Yellow", "Yellow"]


def test_a_missing_value_is_na_and_a_relative_ratio_alone_is_ungraded():
    assert get_grade("m01_revenue_growth", NAN) == "N/A" and get_grade("m01_revenue_growth", None) == "N/A"
    assert get_grade("m02_operating_profit_growth", 0.1) == "Ungraded"      # needs revenue growth: use grade_growth_metrics


def test_profit_and_asset_growth_are_judged_against_revenue_growth():
    base = {"m01_revenue_growth": 0.10}
    grades = grade_growth_metrics({**base, "m02_operating_profit_growth": 0.14, "m03_net_income_growth": 0.04,
                                   "m04_asset_equity_inventory_growth": 0.20}, {})
    assert grades["m02_operating_profit_growth"] == "Green"        # outgrows revenue
    assert grades["m03_net_income_growth"] == "Yellow"             # positive but lagging
    assert grades["m04_asset_equity_inventory_growth"] == "Red"    # assets outgrow revenue
    low = grade_growth_metrics({**base, "m02_operating_profit_growth": 0.05, "m03_net_income_growth": -0.1,
                                "m04_asset_equity_inventory_growth": 0.05}, {})
    assert low["m02_operating_profit_growth"] == "Red" and low["m03_net_income_growth"] == "Red"
    assert low["m04_asset_equity_inventory_growth"] == "Green"     # revenue outgrows assets
    near = grade_growth_metrics({**base, "m02_operating_profit_growth": 0.11}, {})
    assert near["m02_operating_profit_growth"] == "Yellow"         # within 2 points: "roughly equal"
    assert grade_growth_metrics({"m02_operating_profit_growth": 0.1}, {})["m02_operating_profit_growth"] == "Ungraded"


def test_margin_trends_decide_gross_margin_and_sga():
    ctx = {"gross_margin_prior": 0.40, "sga_ratio_prior": 0.25}
    gm = lambda v: grade_growth_metrics({"m05_gross_margin": v}, ctx)["m05_gross_margin"]     # noqa: E731
    assert (gm(0.38), gm(0.405), gm(0.43)) == ("Red", "Yellow", "Green")     # compressing, flat, expanding
    sga = lambda v: grade_growth_metrics({"m10_sga_to_revenue": v}, ctx)["m10_sga_to_revenue"]  # noqa: E731
    assert sga(0.32) == "Red"                      # above 30%
    assert sga(0.27) == "Red"                      # 2 points above last year: rising
    assert sga(0.245) == "Yellow"                  # 20-30% and stable
    assert sga(0.15) == "Green"                    # below 20%
    assert sga(0.22) == "Green"                    # 20-30% but falling
    assert grade_growth_metrics({"m05_gross_margin": 0.4}, {})["m05_gross_margin"] == "Ungraded"   # no prior year


def test_sustainable_growth_free_cash_flow_capex_and_reinvestment_rules():
    g = lambda metrics, ctx=None: grade_growth_metrics(metrics, ctx or {})                      # noqa: E731
    # m15: actual growth (m01) against the sustainable rate
    assert g({"m01_revenue_growth": 0.10, "m15_sustainable_growth_rate": 0.30})["m15_sustainable_growth_rate"] == "Green"
    assert g({"m01_revenue_growth": 0.12, "m15_sustainable_growth_rate": 0.10})["m15_sustainable_growth_rate"] == "Yellow"
    assert g({"m01_revenue_growth": 0.30, "m15_sustainable_growth_rate": 0.10})["m15_sustainable_growth_rate"] == "Red"
    # m17: negative or declining is Red; growing with conversion of at least 70% is Green
    ctx = {"fcf_prior": 100.0, "net_income": 120.0}
    assert g({"m17_free_cash_flow": 130.0}, ctx)["m17_free_cash_flow"] == "Green"      # growing, 108% of net income
    assert g({"m17_free_cash_flow": 90.0}, ctx)["m17_free_cash_flow"] == "Red"         # declining
    assert g({"m17_free_cash_flow": -5.0}, ctx)["m17_free_cash_flow"] == "Red"
    assert g({"m17_free_cash_flow": 105.0}, {"fcf_prior": 100.0, "net_income": 200.0})["m17_free_cash_flow"] == "Yellow"   # 52%
    # m18: the cross-industry median stands in for sector peers
    assert g({"m18_capex_to_sales": 0.01}, {"capex_to_sales_prior": 0.012})["m18_capex_to_sales"] == "Green"
    assert g({"m18_capex_to_sales": 0.05}, {"capex_to_sales_prior": 0.05})["m18_capex_to_sales"] == "Yellow"
    assert g({"m18_capex_to_sales": 0.09}, {"capex_to_sales_prior": 0.09})["m18_capex_to_sales"] == "Red"
    assert g({"m18_capex_to_sales": 0.02}, {"capex_to_sales_prior": 0.005})["m18_capex_to_sales"] == "Red"   # rising fast
    # m16: reinvestment together with ROIC
    assert g({"m16_reinvestment_rate": 0.7, "m13_roic": 0.05})["m16_reinvestment_rate"] == "Red"
    assert g({"m16_reinvestment_rate": 0.7, "m13_roic": 0.25})["m16_reinvestment_rate"] == "Green"
    assert g({"m16_reinvestment_rate": 0.03, "m13_roic": 1.0})["m16_reinvestment_rate"] == "Yellow"


def test_growth_context_reads_the_year_before_from_the_statements():
    stm = FakeStatements([
        {"total_revenue": 200.0, "total_gross_profit": 90.0},
        {"total_revenue": 100.0, "total_gross_profit": 40.0, "sga_expense": 20.0, "net_income_continuing": 10.0,
         "depreciation_and_amortization": 5.0, "gross_ppe": 60.0},
        {"gross_ppe": 50.0},
    ])
    ctx = growth_context(stm)
    assert ctx["gross_margin_prior"] == 0.40 and ctx["sga_ratio_prior"] == 0.20
    assert ctx["capex_to_sales_prior"] == 0.10                      # (60 - 50) / 100
    assert ctx["fcf_prior"] == 5.0                                  # 10 + 5 - 10
    assert growth_context(FakeStatements([{"total_revenue": 1.0}]))["gross_margin_prior"] != \
        growth_context(FakeStatements([{"total_revenue": 1.0}]))["gross_margin_prior"]     # NaN when the year is missing


def test_every_metric_has_a_one_line_rule_for_the_model():
    from equity_pipeline.extraction.grading import GRADE_RULES, RELATIVE_GRADED
    from equity_pipeline.sources.inputs import METRIC_INPUTS

    assert set(GRADE_RULES) == set(METRIC_INPUTS)                       # all 25 growth and 11 risk metrics
    assert RELATIVE_GRADED <= set(GRADE_RULES) and len(RELATIVE_GRADED) == 9
    assert all(len(rule) < 140 for rule in GRADE_RULES.values())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
