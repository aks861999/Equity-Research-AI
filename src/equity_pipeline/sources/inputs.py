"""
The single declared list of what the fundamentals pipeline needs from a company's filings.

Everything else derives from this file:

* `sec_fundamentals.py` builds the fallback chain from `INPUTS[...].tags` and refuses to
  hand out a field that is not declared here (`CompanyStatements.get`).
* `coverage.py` explains each missing metric from `METRIC_INPUTS`.
* `tests/test_inputs.py` fails if a calculator reads a field that is not declared, or if
  `METRIC_INPUTS` and the calculators disagree about which fields they use.

To add a calculator input: declare it here first, then read it in the runner.

Three kinds of input:

* direct         OpenBB gives it under this exact name. If OpenBB leaves it empty, `tags`
                 (raw XBRL tags, best first) are tried, then `how` describes any derivation.
* building_block a raw line that a derived input is built from; it has its own tag
                 fallbacks but the calculators never read it.
* derived        built in `sec_fundamentals._derive` from other lines (`how` says how).
                 Never guessed: left empty when its parts are missing.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Input:
    meaning: str
    kind: str                       # "direct" | "building_block" | "derived"
    tags: tuple[str, ...] = ()      # raw XBRL fallbacks, best first (us-gaap and ifrs-full)
    how: str = ""                   # derivation or special rule, in words


INPUTS: dict[str, Input] = {
    # ---- income statement (direct) -------------------------------------------------------
    "total_revenue": Input("Revenue", "direct",
        ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
         "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "Revenue")),
    "total_gross_profit": Input("Revenue minus cost of revenue (many utilities, telecoms and pharma do not report it)",
        "direct", ("GrossProfit",)),
    "total_operating_income": Input("Operating income (some filers, e.g. PFE, do not report it)", "direct",
        ("OperatingIncomeLoss", "ProfitLossFromOperatingActivities"),
        "else revenue minus CostsAndExpenses"),
    "sga_expense": Input("Selling, general and administrative expense", "direct",
        ("SellingGeneralAndAdministrativeExpense",)),
    "net_income_continuing": Input("Net income from continuing operations (margins, growth)", "direct",
        ("IncomeLossFromContinuingOperations", "ProfitLoss", "NetIncomeLoss")),
    "net_income_to_common": Input("Net income to common shareholders (ROE, ROA)", "direct",
        ("NetIncomeLoss", "ProfitLossAttributableToOwnersOfParent")),
    "total_pretax_income": Input("Income before tax", "direct",
        ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
         "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
         "ProfitLossBeforeTax")),
    "income_tax_expense": Input("Income tax expense", "direct",
        ("IncomeTaxExpenseBenefit", "IncomeTaxExpenseContinuingOperations")),
    "depreciation_and_amortization": Input("Depreciation and amortization (already includes amortization)", "direct",
        ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
         "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortisationExpense")),
    # ---- balance sheet (direct) ----------------------------------------------------------
    "total_assets": Input("Total assets", "direct", ("Assets",)),
    "total_liabilities": Input("Total liabilities", "direct", ("Liabilities",),
        "else liabilities-and-equity minus equity"),
    "total_common_equity": Input("Shareholders' equity (book)", "direct",
        ("StockholdersEquity", "EquityAttributableToOwnersOfParent")),
    "total_current_assets": Input("Current assets", "direct", ("AssetsCurrent", "CurrentAssets")),
    "total_current_liabilities": Input("Current liabilities", "direct", ("LiabilitiesCurrent", "CurrentLiabilities")),
    "retained_earnings": Input("Retained earnings (or accumulated deficit)", "direct",
        ("RetainedEarningsAccumulatedDeficit", "RetainedEarnings")),
    "gross_ppe": Input("Property, plant and equipment before depreciation (capex proxy)", "direct",
        ("PropertyPlantAndEquipmentGross",
         "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetBeforeAccumulatedDepreciationAndAmortization")),
    # ---- building blocks -----------------------------------------------------------------
    "cash_and_equivalents": Input("Cash and cash equivalents", "building_block",
        ("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents")),
    "short_term_investments": Input("Short-term investments", "building_block",
        ("ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent")),
    "net_inventory": Input("Inventory", "building_block", ("InventoryNet", "Inventories")),
    "goodwill": Input("Goodwill", "building_block", ("Goodwill",)),
    "intangible_assets": Input("Intangible assets excluding goodwill", "building_block",
        ("IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet")),
    # ---- derived -------------------------------------------------------------------------
    "interest_expense": Input("Interest expense", "derived", (),
        "OpenBB total_interest_expense, else raw expense tags, else cash interest paid (labelled proxy)"),
    "ebit": Input("Earnings before interest and tax", "derived", (),
        "pretax income + interest expense (pretax income alone, flagged, if no interest found)"),
    "short_term_debt_total": Input("Short-term borrowings + current part of long-term debt", "derived", (),
        "OpenBB debt lines, else raw debt tags (larger of DebtCurrent and the sum of its parts)"),
    "long_term_debt_noncurrent": Input("Long-term debt excluding the current part", "derived", (),
        "OpenBB long_term_debt (current part removed only if it equals the filer's total-debt tag), "
        "else raw debt tags. No debt facts and no interest expense = debt-free (assumed_zero)"),
    "cash_and_short_term_investments": Input("Cash plus short-term investments", "derived", (),
        "cash_and_equivalents + short_term_investments"),
    "inventory": Input("Inventory, 0 for companies that hold none", "derived", (), "net_inventory, else 0"),
    "intangibles_total": Input("Goodwill plus other intangibles, 0 if none reported", "derived", (),
        "goodwill + intangible_assets, else 0"),
    "extraordinary_credit": Input("One-off gains", "derived", (), "OpenBB extraordinary_income, else 0"),
    "extraordinary_charge": Input("One-off charges", "derived", (),
        "impairment + restructuring + other special charges, else 0"),
}

FALLBACK_TAGS: dict[str, list[str]] = {name: list(i.tags) for name, i in INPUTS.items() if i.tags}
CALCULATOR_INPUTS = {name for name, i in INPUTS.items() if i.kind != "building_block"}

# metric -> the (field, fiscal-year offset) inputs it needs. Offset 0 = latest year.
_STD, _LTD = ("short_term_debt_total", 0), ("long_term_debt_noncurrent", 0)
_REV, _EBIT, _DA = ("total_revenue", 0), ("ebit", 0), ("depreciation_and_amortization", 0)
_CASH, _CA, _CL = ("cash_and_short_term_investments", 0), ("total_current_assets", 0), ("total_current_liabilities", 0)
_EQ, _TA = ("total_common_equity", 0), ("total_assets", 0)
_NI, _NIC = ("net_income_continuing", 0), ("net_income_to_common", 0)
_PPE0, _PPE1 = ("gross_ppe", 0), ("gross_ppe", 1)
METRIC_INPUTS: dict[str, list[tuple[str, int]]] = {
    "m01_revenue_growth": [_REV, ("total_revenue", 1)],
    "m02_operating_profit_growth": [_EBIT, ("ebit", 2)],
    "m03_net_income_growth": [_NI, ("net_income_continuing", 2)],
    "m04_asset_equity_inventory_growth": [_TA, ("total_assets", 1)],
    "m05_gross_margin": [("total_gross_profit", 0), _REV],
    "m06_operating_margin": [("total_operating_income", 0), _REV],
    "m07_ebit_margin": [_EBIT, _REV],
    "m08_ebitda_margin": [_EBIT, _DA, _REV],
    "m09_net_profit_margin": [_NI, _REV],
    "m10_sga_to_revenue": [("sga_expense", 0), _REV],
    "m11_roe": [_NIC, _EQ, ("total_common_equity", 1)],
    "m12_roa": [_NIC, _TA, ("total_assets", 1)],
    "m13_roic": [_EBIT, _STD, _LTD, _EQ, _CASH, ("income_tax_expense", 0), ("total_pretax_income", 0)],
    "m14_asset_turnover": [_REV, _TA, ("total_assets", 1)],
    "m15_sustainable_growth_rate": [_NIC, _EQ, ("total_common_equity", 1), ("retained_earnings", 0), ("retained_earnings", 1)],
    "m16_reinvestment_rate": [_PPE0, _PPE1, _EBIT],
    "m17_free_cash_flow": [_NI, _DA, _PPE0, _PPE1],
    "m18_capex_to_sales": [_PPE0, _PPE1, _REV],
    "m19_debt_to_equity": [_STD, _LTD, _EQ],
    "m20_debt_to_assets": [_STD, _LTD, _TA],
    "m21_net_debt_to_ebitda": [_STD, _LTD, _CASH, _EBIT, _DA],
    "m22_current_ratio": [_CA, _CL],
    "m23_quick_ratio": [_CA, ("inventory", 0), _CL],
    "m24_cash_ratio": [_CASH, _CL],
    "m25_interest_coverage": [_EBIT, ("interest_expense", 0)],
    "r01_altman_z_score": [_TA, ("total_liabilities", 0), _CA, _CL, ("retained_earnings", 0), _EBIT, _EQ],
    "r02_debt_to_equity": [_STD, _LTD, _EQ],
    "r03_debt_ratio": [("total_liabilities", 0), _TA],
    "r04_net_debt_to_ebitda": [_STD, _LTD, _CASH, _EBIT, _DA],
    "r05_short_term_debt_ratio": [_STD, _LTD],
    "r06_current_ratio": [_CA, _CL],
    "r07_quick_ratio": [_CA, ("inventory", 0), _CL],
    "r08_interest_coverage": [_EBIT, ("interest_expense", 0)],
    "r09_retained_earnings_deficit": [("retained_earnings", 0), _TA],
    "r10_non_recurring_items": [("total_pretax_income", 0), ("extraordinary_credit", 0), ("extraordinary_charge", 0)],
    "r11_goodwill_intangibles": [("intangibles_total", 0), _TA],
}

for _metric, _pairs in METRIC_INPUTS.items():
    for _field, _ in _pairs:
        if _field not in CALCULATOR_INPUTS:
            raise ValueError(f"{_metric} needs '{_field}', which is not a declared calculator input in inputs.py")
