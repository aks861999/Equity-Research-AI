"""
Red / Yellow / Green grades of the 25 growth ratios.

The rules follow the project's design document, "Part 10: Decision-Boundary Table (PM Screening
Thresholds)". Every boundary and the reason for it is written down in docs/grading-rules.md. That document gives, for each of the 25 ratios, what is Red (concern), Yellow (acceptable,
monitor) and Green (strong). Three kinds of rule:

  1. LEVEL BANDS: a plain number decides (revenue growth below 10% is Red, ...). `_BANDS` and
     `_current_ratio`. 15 of the 25.
  2. TREND AND RELATIVE RULES: the ratio is compared with another figure of the SAME company: profit
     growth with revenue growth (m02, m03, m04), the sustainable growth rate with actual growth (m15),
     the gross margin and SG&A ratio with last year's (m05, m10), free cash flow with last year's and
     with net income (m17), capex with last year's (m18), reinvestment with ROIC (m16).
     `growth_context` computes the extra figures these rules need; `grade_growth_metrics` applies them.

The document says in words what "roughly equal", "far" or "high" mean only in some places. Where it gives
no number, a constant below is used. Each of these is marked ASSUMPTION: change the constant, not the
logic, if you want a different reading. One rule cannot follow the document exactly: m18 compares capex
with SECTOR PEERS, and the project has no peer groups, so the document's own cross-industry median
(3.7% of sales) stands in (also marked ASSUMPTION).

A ratio that has no value is "N/A". A ratio whose rule needs another figure that is missing (for example
last year's gross margin) is "Ungraded".
"""
from __future__ import annotations

import math

# --- ASSUMPTIONS: numbers the design document does not give --------------------------------------------
TOL_GROWTH = 0.02         # "roughly equal" growth rates: within 2 percentage points (m02, m03, m04)
TOL_MARGIN_TREND = 0.01   # a margin that moves by less than 1 percentage point is "flat" (m05, m10)
SGR_MODEST_EXCESS = 0.05  # growth above the sustainable rate by up to 5 points is "modest", more is "far" (m15)
REINVEST_HIGH = 0.50      # reinvestment rate (net capex / NOPAT) of 50% or more is "high" (m16)
PEER_CAPEX_MEDIAN = 0.037  # document: "cross-industry median is ~3.7% of sales"; stands in for the peer median (m18)
CAPEX_RISE_TOL = 0.01     # capex-to-sales up by more than 1 point of sales in a year is "rising abnormally fast" (m18)
# --- numbers the document does give ---------------------------------------------------------------------
FCF_CONVERSION_MIN = 0.70  # free cash flow of at least 70% of net income (m17)
ROIC_LOW, ROIC_HIGH = 0.08, 0.15   # m13 bands, also used by the m16 rule
SGA_HIGH, SGA_LOW = 0.30, 0.20     # m10: above 30% Red, below 20% Green

# metric -> (direction, red cutoff, green cutoff)
#   "high": higher is better. Red below the first number, Green from the second.
#   "low":  lower is better.  Red above the first number, Green up to the second.
_BANDS = {
    "m01_revenue_growth":     ("high", 0.10, 0.15),
    "m06_operating_margin":   ("high", 0.10, 0.20),
    "m07_ebit_margin":        ("high", 0.10, 0.20),
    "m08_ebitda_margin":      ("high", 0.08, 0.15),
    "m09_net_profit_margin":  ("high", 0.05, 0.10),
    "m11_roe":                ("high", 0.10, 0.15),
    "m12_roa":                ("high", 0.05, 0.10),
    "m13_roic":               ("high", ROIC_LOW, ROIC_HIGH),
    "m14_asset_turnover":     ("high", 1.0, 1.5),
    "m19_debt_to_equity":     ("low", 2.0, 1.0),
    "m20_debt_to_assets":     ("low", 0.5, 0.3),
    "m21_net_debt_to_ebitda": ("low", 4.0, 2.0),
    "m23_quick_ratio":        ("high", 0.5, 1.0),
    "m24_cash_ratio":         ("high", 0.2, 0.5),
    "m25_interest_coverage":  ("high", 1.5, 3.0),
}


# Metrics whose grade is NOT a judgement of the level alone: it compares with revenue growth, with last
# year or with another ratio. A high level can carry a Red grade there (a gross margin of 48% that fell).
RELATIVE_GRADED = frozenset({
    "m02_operating_profit_growth", "m03_net_income_growth", "m04_asset_equity_inventory_growth", "m05_gross_margin",
    "m10_sga_to_revenue", "m15_sustainable_growth_rate", "m16_reinvestment_rate", "m17_free_cash_flow",
    "m18_capex_to_sales",
})

# The rule behind each grade in one line. It travels with every metric to the language model, so the
# model can say what a grade means instead of guessing (and the web terminal can show it).
GRADE_RULES = {
    "m01_revenue_growth": "Level: Red <10%, Yellow 10-15%, Green >=15%",
    "m02_operating_profit_growth": "Vs revenue growth: Red if below it or negative, Yellow if about equal (within 2 pts), Green if above",
    "m03_net_income_growth": "Vs revenue growth: Red if negative, Yellow if positive but below it, Green if at or above",
    "m04_asset_equity_inventory_growth": "Vs revenue growth: Red if assets grow faster, Yellow if about equal (within 2 pts), Green if revenue grows faster",
    "m05_gross_margin": "Trend vs last year (the level is not graded): Red if down >1 pt, Yellow if within 1 pt, Green if up >1 pt",
    "m06_operating_margin": "Level: Red <10%, Yellow 10-20%, Green >20%",
    "m07_ebit_margin": "Level: Red <10%, Yellow 10-20%, Green >20%",
    "m08_ebitda_margin": "Level: Red <8%, Yellow 8-15%, Green >15%",
    "m09_net_profit_margin": "Level: Red <5%, Yellow 5-10%, Green >10%",
    "m10_sga_to_revenue": "Level and trend: Red if >30% or up >1 pt on last year, Green if <20% or down >1 pt, else Yellow",
    "m11_roe": "Level: Red <10%, Yellow 10-15%, Green >=15%",
    "m12_roa": "Level: Red <5%, Yellow 5-10%, Green >=10%",
    "m13_roic": "Level: Red <8%, Yellow 8-15%, Green >=15%",
    "m14_asset_turnover": "Level: Red <1.0, Yellow 1.0-1.5, Green >=1.5",
    "m15_sustainable_growth_rate": "Vs revenue growth: Green if growth <= this rate, Yellow if above it by up to 5 pts, Red if by more",
    "m16_reinvestment_rate": "With ROIC: Red if >=50% of NOPAT is reinvested and ROIC <8%, Green if >=50% and ROIC >=15%, else Yellow",
    "m17_free_cash_flow": "Red if <=0 or below last year; Green if growing and >=70% of net income; else Yellow",
    "m18_capex_to_sales": "Red if >7.4% of sales or up >1 pt on last year; Green if <=3.7% (cross-industry median) and not rising; else Yellow",
    "m19_debt_to_equity": "Level (lower is better): Red >2.0, Yellow 1.0-2.0, Green <=1.0",
    "m20_debt_to_assets": "Level (lower is better): Red >0.5, Yellow 0.3-0.5, Green <=0.3",
    "m21_net_debt_to_ebitda": "Level (lower is better): Red >4.0, Yellow 2.0-4.0, Green <=2.0",
    "m22_current_ratio": "Level band: Red <1.0, Yellow 1.0-1.5 or >2.0, Green 1.5-2.0",
    "m23_quick_ratio": "Level: Red <0.5, Yellow 0.5-1.0, Green >=1.0",
    "m24_cash_ratio": "Level: Red <0.2, Yellow 0.2-0.5, Green >=0.5",
    "m25_interest_coverage": "Level: Red <1.5x, Yellow 1.5-3.0x, Green >3.0x",
    # risk set (extraction/run_risk_fundamentals.py): Red = high risk
    "r01_altman_z_score": "Level (higher is safer): Red <1.1, Yellow 1.1-2.6, Green >=2.6",
    "r02_debt_to_equity": "Level (lower is safer): Red >2.0, Yellow 1.0-2.0, Green <=1.0",
    "r03_debt_ratio": "Level (lower is safer): Red >0.5, Yellow 0.3-0.5, Green <=0.3",
    "r04_net_debt_to_ebitda": "Level (lower is safer): Red >4.0, Yellow 2.0-4.0, Green <=2.0",
    "r05_short_term_debt_ratio": "Level (lower is safer): Red >0.5, Yellow 0.3-0.5, Green <=0.3",
    "r06_current_ratio": "Level: Red <1.0, Yellow 1.0-1.5, Green >=1.5",
    "r07_quick_ratio": "Level: Red <0.5, Yellow 0.5-1.0, Green >=1.0",
    "r08_interest_coverage": "Level: Red <1.5x, Yellow 1.5-3.0x, Green >=3.0x",
    "r09_retained_earnings_deficit": "Level: Red <0, Yellow 0-0.2, Green >=0.2",
    "r10_non_recurring_items": "Level (lower is better): Red >0.05, Yellow above 0 up to 0.05, Green 0",
    "r11_goodwill_intangibles": "Level (lower is safer): Red >0.4, Yellow 0.2-0.4, Green <=0.2",
}


def _fin(value) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _div(a, b) -> float:
    return float(a) / float(b) if _fin(a) and _fin(b) and float(b) != 0 else float("nan")


def _current_ratio(v: float) -> str:
    """Document: Red below 1.0; Yellow 1.0 to 1.5 or above 2.5; Green 1.5 to 2.0. Between 2.0 and 2.5 the
    document is silent: Yellow (ASSUMPTION), so only a shortage of liquidity is Red."""
    if v < 1.0:
        return "Red"
    if v < 1.5:
        return "Yellow"
    return "Green" if v <= 2.0 else "Yellow"


def get_grade(metric_id: str, value) -> str:
    """The grade of a ratio that is judged by its own number alone (the 15 level bands and m22).
    "N/A" without a value; "Ungraded" for a ratio that needs another figure (use grade_growth_metrics)."""
    if not _fin(value):
        return "N/A"
    v = float(value)
    if metric_id == "m22_current_ratio":
        return _current_ratio(v)
    band = _BANDS.get(metric_id)
    if band is None:
        return "Ungraded"
    direction, red, green = band
    if direction == "high":
        return "Red" if v < red else "Yellow" if v < green else "Green"
    return "Red" if v > red else "Yellow" if v > green else "Green"


def growth_context(stm) -> dict[str, float]:
    """The extra figures the trend and relative rules need, from the same statements as the ratios
    (offset 0 = latest fiscal year, 1 = the year before, 2 = two years before). NaN when not available."""
    v = stm.get
    rev0, rev1 = v(0, "total_revenue"), v(1, "total_revenue")
    capex_prior = (v(1, "gross_ppe") - v(2, "gross_ppe")) if _fin(v(1, "gross_ppe")) and _fin(v(2, "gross_ppe")) else float("nan")
    ni1, da1 = v(1, "net_income_continuing"), v(1, "depreciation_and_amortization")
    return {
        "gross_margin_prior": _div(v(1, "total_gross_profit"), rev1),
        "sga_ratio_prior": _div(v(1, "sga_expense"), rev1),
        "capex_to_sales_prior": _div(capex_prior, rev1),
        "fcf_prior": (ni1 + da1 - capex_prior) if _fin(ni1) and _fin(da1) and _fin(capex_prior) else float("nan"),
        "net_income": v(0, "net_income_continuing"),
        "revenue": rev0,
    }


def _grade_one(metric_id: str, v: float, metrics: dict, ctx: dict) -> str:
    rev_growth = metrics.get("m01_revenue_growth")

    if metric_id in _BANDS or metric_id == "m22_current_ratio":
        return get_grade(metric_id, v)

    if metric_id == "m02_operating_profit_growth":           # Red: below revenue growth or negative; Green: above it
        if not _fin(rev_growth):
            return "Ungraded"
        if v < 0 or v < rev_growth - TOL_GROWTH:
            return "Red"
        return "Green" if v > rev_growth + TOL_GROWTH else "Yellow"

    if metric_id == "m03_net_income_growth":                 # Red: negative; Yellow: positive but lagging; Green: at or above
        if v < 0:
            return "Red"
        if not _fin(rev_growth):
            return "Ungraded"
        return "Green" if v >= rev_growth - TOL_GROWTH else "Yellow"

    if metric_id == "m04_asset_equity_inventory_growth":     # Red: assets outgrow revenue; Green: revenue outgrows assets
        if not _fin(rev_growth):
            return "Ungraded"
        if v > rev_growth + TOL_GROWTH:
            return "Red"
        return "Green" if v < rev_growth - TOL_GROWTH else "Yellow"

    if metric_id == "m05_gross_margin":                      # a TREND: compressing Red, flat Yellow, expanding Green
        prior = ctx.get("gross_margin_prior")
        if not _fin(prior):
            return "Ungraded"
        delta = v - prior
        return "Red" if delta < -TOL_MARGIN_TREND else "Green" if delta > TOL_MARGIN_TREND else "Yellow"

    if metric_id == "m10_sga_to_revenue":                    # lower is better; direction matters as much as the level
        prior = ctx.get("sga_ratio_prior")
        delta = v - prior if _fin(prior) else float("nan")
        rising = _fin(delta) and delta > TOL_MARGIN_TREND
        falling = _fin(delta) and delta < -TOL_MARGIN_TREND
        if v > SGA_HIGH or rising:
            return "Red"
        return "Green" if v < SGA_LOW or falling else "Yellow"

    if metric_id == "m15_sustainable_growth_rate":           # Green: actual growth does not exceed the sustainable rate
        if not _fin(rev_growth):
            return "Ungraded"
        excess = rev_growth - v
        return "Green" if excess <= 0 else "Yellow" if excess <= SGR_MODEST_EXCESS else "Red"

    if metric_id == "m16_reinvestment_rate":                 # high reinvestment at a low ROIC is Red, at a high ROIC Green
        roic = metrics.get("m13_roic")
        if _fin(roic) and v >= REINVEST_HIGH:
            if roic < ROIC_LOW:
                return "Red"
            if roic >= ROIC_HIGH:
                return "Green"
        return "Yellow"                                      # no such pattern: acceptable, monitor

    if metric_id == "m17_free_cash_flow":                    # Red: negative or declining; Green: growing and >= 70% of net income
        prior, ni = ctx.get("fcf_prior"), ctx.get("net_income")
        if v <= 0 or (_fin(prior) and v < prior):
            return "Red"
        conversion = _div(v, ni) if _fin(ni) and float(ni) > 0 else float("nan")
        return "Green" if _fin(conversion) and conversion >= FCF_CONVERSION_MIN else "Yellow"

    if metric_id == "m18_capex_to_sales":                    # the document compares with sector peers (see the module docstring)
        prior = ctx.get("capex_to_sales_prior")
        rising = _fin(prior) and (v - prior) > CAPEX_RISE_TOL
        if v > 2 * PEER_CAPEX_MEDIAN or rising:
            return "Red"
        return "Green" if v <= PEER_CAPEX_MEDIAN else "Yellow"

    return "Ungraded"


def grade_growth_metrics(metrics: dict[str, float], context: dict[str, float]) -> dict[str, str]:
    """metric id -> "Red" | "Yellow" | "Green" | "Ungraded" (an input of the rule is missing) | "N/A" (no value)."""
    return {metric_id: ("N/A" if not _fin(value) else _grade_one(metric_id, float(value), metrics, context))
            for metric_id, value in metrics.items()}
