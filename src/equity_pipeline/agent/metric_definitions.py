"""
Clean, plain-English descriptions of every fundamental metric the agent
reasons over.

Why this file exists: the LLM is only as good as the context it is given.
Feeding it a bare number like `m13_roic = 0.0421` invites misreading. Every
metric passed into the prompt is instead wrapped with (a) its human name,
(b) a one-line definition of what it measures, and (c) a hint about which
direction is "good" for a growth strategy. This is pure static text — no
API calls, no randomness — so it never affects reproducibility.

Metric IDs and thresholds mirror config/metrics.yaml and
extraction/run_growth_fundamentals.py / run_risk_fundamentals.py exactly.
Do not rename ids here without renaming them everywhere else too.
"""

# --- Growth-strategy fundamentals (25 metrics, m01-m25) ---------------------
# Values come from fundamental_metrics table (fundamental_value/grade) and,
# where available, from metric_scores table (weighted_sentiment/dispersion/
# volume from Google-News-sourced articles matched to that specific metric).
GROWTH_METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    "m01_revenue_growth": {
        "name": "Revenue Growth (YoY / CAGR)",
        "description": "How fast top-line sales are growing. The single most direct signal of whether a company is still in growth mode.",
        "good_direction": "higher is better for a growth strategy",
    },
    "m02_operating_profit_growth": {
        "name": "Operating Profit Growth (EBIT)",
        "description": "Growth in profit from core operations, before interest and tax. Shows whether growth is translating into operating leverage, not just top-line noise.",
        "good_direction": "higher is better",
    },
    "m03_net_income_growth": {
        "name": "Net Income Growth",
        "description": "Growth in bottom-line profit after all costs, interest and tax. Confirms growth is reaching shareholders, not being absorbed by costs.",
        "good_direction": "higher is better",
    },
    "m04_asset_equity_inventory_growth": {
        "name": "Asset / Balance-Sheet Growth",
        "description": "How fast the balance sheet (total assets) is expanding year over year. Useful as a cross-check that the business is actually scaling up capacity.",
        "good_direction": "higher generally supports growth, but should not outpace revenue growth",
    },
    "m05_gross_margin": {
        "name": "Gross Margin",
        "description": "Gross profit as a share of revenue. Reflects pricing power and unit economics before overhead.",
        "good_direction": "higher is better",
    },
    "m06_operating_margin": {
        "name": "Operating Margin",
        "description": "Operating income as a share of revenue. Shows how efficiently the company converts sales into operating profit.",
        "good_direction": "higher is better",
    },
    "m07_ebit_margin": {
        "name": "EBIT Margin",
        "description": "EBIT as a share of revenue, a close cousin of operating margin. Confirms core profitability trend.",
        "good_direction": "higher is better",
    },
    "m08_ebitda_margin": {
        "name": "EBITDA Margin",
        "description": "Profitability before depreciation/amortization is stripped out, often used to compare capital-intensity-adjusted profitability across companies.",
        "good_direction": "higher is better",
    },
    "m09_net_profit_margin": {
        "name": "Net Profit Margin",
        "description": "Net income as a share of revenue — the ultimate 'how much of every euro/dollar of sales becomes profit' number.",
        "good_direction": "higher is better",
    },
    "m10_sga_to_revenue": {
        "name": "SG&A-to-Revenue",
        "description": "Selling, general & administrative expense as a share of revenue. A rising ratio can signal cost discipline is slipping even while growing.",
        "good_direction": "lower is generally better (more operating leverage)",
    },
    "m11_roe": {
        "name": "Return on Equity (ROE)",
        "description": "Net income relative to average shareholder equity. Measures how efficiently the company generates profit from shareholders' capital.",
        "good_direction": "higher is better",
    },
    "m12_roa": {
        "name": "Return on Assets (ROA)",
        "description": "Net income relative to average total assets. Measures how efficiently the whole asset base is used to generate profit.",
        "good_direction": "higher is better",
    },
    "m13_roic": {
        "name": "Return on Invested Capital (ROIC)",
        "description": "After-tax operating profit (NOPAT) relative to invested capital (debt + equity - cash). The key test of whether growth is value-creating rather than just capital-absorbing.",
        "good_direction": "higher is better, especially above the cost of capital",
    },
    "m14_asset_turnover": {
        "name": "Asset Turnover",
        "description": "Revenue generated per unit of average assets. Measures capital efficiency / how hard the asset base is working.",
        "good_direction": "higher is better",
    },
    "m15_sustainable_growth_rate": {
        "name": "Sustainable Growth Rate",
        "description": "The growth rate a company can fund from retained earnings alone (ROE x retention ratio), without new debt or equity. Tests whether current growth is self-funded or reliant on external capital.",
        "good_direction": "higher, and ideally close to or above actual revenue growth, is better",
    },
    "m16_reinvestment_rate": {
        "name": "Reinvestment Rate",
        "description": "Net capital expenditure relative to after-tax operating profit. Shows how much of the company's profit is being ploughed back into growing the asset base.",
        "good_direction": "context-dependent: too low may signal under-investment, too high may signal poor capital discipline",
    },
    "m17_free_cash_flow": {
        "name": "Free Cash Flow (approx.)",
        "description": "Net income plus depreciation/amortization minus net capex — an approximation of cash generated after reinvestment needs.",
        "good_direction": "higher (and positive) is better",
    },
    "m18_capex_to_sales": {
        "name": "Capex-to-Sales",
        "description": "Capital expenditure as a share of revenue. Indicates how capital-intensive the growth strategy is.",
        "good_direction": "context-dependent, watch the trend rather than the absolute level",
    },
    "m19_debt_to_equity": {
        "name": "Debt-to-Equity",
        "description": "Total debt relative to shareholder equity. A leverage measure — how much of the balance sheet is debt-funded.",
        "good_direction": "lower is generally safer, but some leverage is normal",
    },
    "m20_debt_to_assets": {
        "name": "Debt-to-Assets",
        "description": "Total debt relative to total assets. Another leverage measure, showing what fraction of assets are debt-financed.",
        "good_direction": "lower is generally safer",
    },
    "m21_net_debt_to_ebitda": {
        "name": "Net Debt-to-EBITDA",
        "description": "Net debt relative to EBITDA — a common measure of how many years of cash earnings it would take to pay off net debt.",
        "good_direction": "lower is generally safer",
    },
    "m22_current_ratio": {
        "name": "Current Ratio",
        "description": "Current assets relative to current liabilities. Basic short-term liquidity / ability to cover near-term obligations.",
        "good_direction": "roughly 1.0-2.5 is typically considered healthy; too high can indicate idle capital",
    },
    "m23_quick_ratio": {
        "name": "Quick Ratio",
        "description": "Current assets excluding inventory, relative to current liabilities. A stricter liquidity test than the current ratio.",
        "good_direction": "higher is generally safer",
    },
    "m24_cash_ratio": {
        "name": "Cash Ratio",
        "description": "Cash and short-term investments relative to current liabilities. The strictest liquidity measure.",
        "good_direction": "higher is generally safer",
    },
    "m25_interest_coverage": {
        "name": "Interest Coverage",
        "description": "EBIT relative to interest expense. Measures how comfortably operating profit covers debt interest payments.",
        "good_direction": "higher is safer",
    },
}

# --- Risk-screening fundamentals (11 of 12 metrics are actually computed) --
# NOTE: r01_altman_z_score is intentionally excluded — it requires market
# capitalization, which is not present in BalanceSheets.csv / IncomeStatements.csv,
# and run_risk_fundamentals.py skips it rather than compute it on bad data.
# There is no per-metric news sentiment for risk metrics (Google News fetch
# for the risk theme was not run), so these carry fundamental_value/grade
# only. General, company-level sentiment (not metric-specific) from Yahoo
# Finance news and SEC EDGAR filings is supplied separately as extra context
# for the risk question.
RISK_METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    "r02_debt_to_equity": {
        "name": "Debt-to-Equity Ratio",
        "description": "Total debt relative to shareholder equity. High values mean the company relies heavily on debt financing.",
        "good_direction": "lower is lower-risk",
    },
    "r03_debt_ratio": {
        "name": "Debt Ratio (Total Liabilities / Total Assets)",
        "description": "Share of total assets financed by liabilities of any kind (not just debt). A broad solvency measure.",
        "good_direction": "lower is lower-risk",
    },
    "r04_net_debt_to_ebitda": {
        "name": "Net Debt-to-EBITDA",
        "description": "Net debt relative to cash earnings (EBITDA). A high multiple signals debt could become hard to service if earnings soften.",
        "good_direction": "lower is lower-risk",
    },
    "r05_short_term_debt_ratio": {
        "name": "Short-Term Debt / Total Debt",
        "description": "Share of total debt that must be refinanced or repaid in the near term. High values signal refinancing risk.",
        "good_direction": "lower is lower-risk",
    },
    "r06_current_ratio": {
        "name": "Current Ratio",
        "description": "Current assets relative to current liabilities. Low values signal the company may struggle to meet short-term obligations.",
        "good_direction": "higher (up to a point) is lower-risk",
    },
    "r07_quick_ratio": {
        "name": "Quick Ratio",
        "description": "Current assets excluding inventory, relative to current liabilities. Stricter than the current ratio; inventory can be slow/impossible to liquidate.",
        "good_direction": "higher is lower-risk",
    },
    "r08_interest_coverage": {
        "name": "Interest Coverage Ratio",
        "description": "EBIT relative to interest expense. Low coverage signals the company could struggle to service its debt from operating profit alone.",
        "good_direction": "higher is lower-risk",
    },
    "r09_retained_earnings_deficit": {
        "name": "Retained Earnings / Total Assets",
        "description": "Accumulated retained earnings (or deficit) relative to total assets. A negative or very low value can signal a history of losses eroding equity.",
        "good_direction": "higher (positive) is lower-risk",
    },
    "r10_non_recurring_items": {
        "name": "Non-Recurring / Extraordinary Items (earnings quality)",
        "description": "Extraordinary charges/credits relative to pretax income. Large values suggest reported earnings are being distorted by one-off items, reducing earnings-quality confidence.",
        "good_direction": "lower is lower-risk (cleaner earnings)",
    },
    "r11_goodwill_intangibles": {
        "name": "Goodwill & Intangibles / Total Assets",
        "description": "Share of the balance sheet made up of goodwill/intangibles from past acquisitions. High values create impairment risk if those assets lose value.",
        "good_direction": "lower is lower-risk",
    },
    "r12_risk_provision_trend": {
        "name": "Risk-Provision / Total Liabilities",
        "description": "Provisions for legal, warranty or other risk charges relative to total liabilities. Rising provisions can foreshadow litigation, warranty, or restructuring costs.",
        "good_direction": "lower is lower-risk",
    },
}


def growth_metric_ids() -> list[str]:
    return list(GROWTH_METRIC_DEFINITIONS.keys())


def risk_metric_ids() -> list[str]:
    return list(RISK_METRIC_DEFINITIONS.keys())