import os
import logging
import pandas as pd
import numpy as np
from pathlib import Path

from ..sources.sec_fundamentals import configured_tickers, load_company_statements
from .fundamentals_store import ensure_table, upsert_metric

logger = logging.getLogger("run_fundamentals")

def get_grade(metric_id, value):
    """Applies Part 10 Decision-Boundary Thresholds."""
    if value is None or pd.isna(value):
        return "N/A"
    
    val = float(value)
    if metric_id == "m01_revenue_growth":
        if val < 0.10: return "Red"
        elif val < 0.15: return "Yellow"
        else: return "Green"
    elif metric_id == "m05_gross_margin":
        if val < 0.20: return "Red"
        elif val < 0.40: return "Yellow"
        else: return "Green"
    elif metric_id in ["m06_operating_margin", "m07_ebit_margin"]:
        if val < 0.10: return "Red"
        elif val < 0.20: return "Yellow"
        else: return "Green"
    elif metric_id == "m11_roe":
        if val < 0.10: return "Red"
        elif val < 0.15: return "Yellow"
        else: return "Green"
    elif metric_id == "m13_roic":
        if val < 0.08: return "Red"
        elif val < 0.15: return "Yellow"
        else: return "Green"
    elif metric_id == "m19_debt_to_equity":
        if val > 2.0: return "Red"
        elif val > 1.0: return "Yellow"
        else: return "Green"
    elif metric_id == "m21_net_debt_to_ebitda":
        if val > 4.0: return "Red"
        elif val > 2.0: return "Yellow"
        else: return "Green"
    elif metric_id == "m22_current_ratio":
        if val < 1.0 or val > 2.5: return "Red"
        elif (val >= 1.0 and val < 1.5) or val > 2.0: return "Yellow"
        else: return "Green"
    else:
        return "Ungraded" # For metrics that require trend/peer comparison

def compute_growth_metrics(stm) -> dict[str, float]:
    """The 25 growth-screening ratios for one company (NaN where an input is missing).
    Pure function of the SEC statements: no database, so the coverage report can reuse it.
    Variable suffixes: _25 = latest fiscal year, _24 = the year before, _23 = two years before."""
    v = stm.get  # v(offset, openbb_field): offset 0 = latest fiscal year

    # IS Data points
    rev_23, rev_24, rev_25 = v(2, 'total_revenue'), v(1, 'total_revenue'), v(0, 'total_revenue')
    ebit_25 = v(0, 'ebit')
    da_25 = v(0, 'depreciation_and_amortization')
    gross_inc_25 = v(0, 'total_gross_profit')
    op_inc_25 = v(0, 'total_operating_income')
    sga_25 = v(0, 'sga_expense')
    ni_25 = v(0, 'net_income_continuing')   # margins
    ni_roe_25 = v(0, 'net_income_to_common')  # ROE / ROA
    pretax_25 = v(0, 'total_pretax_income')
    tax_25 = v(0, 'income_tax_expense')
    int_exp_25 = v(0, 'interest_expense')

    # BS Data points
    assets_24, assets_25 = v(1, 'total_assets'), v(0, 'total_assets')
    eq_24, eq_25 = v(1, 'total_common_equity'), v(0, 'total_common_equity')
    std_25, ltd_25 = v(0, 'short_term_debt_total'), v(0, 'long_term_debt_noncurrent')
    cash_25 = v(0, 'cash_and_short_term_investments')
    ca_25, cl_25 = v(0, 'total_current_assets'), v(0, 'total_current_liabilities')
    inv_25 = v(0, 'inventory')
    re_24, re_25 = v(1, 'retained_earnings'), v(0, 'retained_earnings')
    ppe_24, ppe_25 = v(1, 'gross_ppe'), v(0, 'gross_ppe')

    # Calculate 25 Metrics
    metrics = {}
    # 1. Growth (Using 2-year CAGR where 2023 data exists, as per Part 1.1)
    metrics["m01_revenue_growth"] = (rev_25 / rev_23)**0.5 - 1 if (rev_23 and rev_23 > 0) else (rev_25 - rev_24) / rev_24 if rev_24 else np.nan
    ebit_23, ni_23 = v(2, 'ebit'), v(2, 'net_income_continuing')
    # A 2-year CAGR is only defined when both ends are positive (a negative ratio has no real root).
    metrics["m02_operating_profit_growth"] = (ebit_25 / ebit_23)**0.5 - 1 if (ebit_23 > 0 and ebit_25 > 0) else np.nan
    metrics["m03_net_income_growth"] = (ni_25 / ni_23)**0.5 - 1 if (ni_23 > 0 and ni_25 > 0) else np.nan
    metrics["m04_asset_equity_inventory_growth"] = (assets_25 - assets_24) / assets_24 if assets_24 else np.nan

    # 2. Profitability
    metrics["m05_gross_margin"] = gross_inc_25 / rev_25 if rev_25 else np.nan
    metrics["m06_operating_margin"] = op_inc_25 / rev_25 if rev_25 else np.nan
    metrics["m07_ebit_margin"] = ebit_25 / rev_25 if rev_25 else np.nan
    metrics["m08_ebitda_margin"] = (ebit_25 + da_25) / rev_25 if rev_25 else np.nan
    metrics["m09_net_profit_margin"] = ni_25 / rev_25 if rev_25 else np.nan
    metrics["m10_sga_to_revenue"] = sga_25 / rev_25 if rev_25 else np.nan

    # 3. Capital Efficiency
    avg_eq = (eq_24 + eq_25) / 2 if (eq_24 and eq_25) else np.nan
    avg_assets = (assets_24 + assets_25) / 2 if (assets_24 and assets_25) else np.nan
    tax_rate = tax_25 / pretax_25 if pretax_25 and pretax_25 > 0 else 0.2 # fallback to 20% if negative pretax
    nopat_25 = ebit_25 * (1 - tax_rate) if ebit_25 else np.nan
    inv_cap_25 = (std_25 + ltd_25 + eq_25 - cash_25) if (std_25 is not None and ltd_25 is not None and eq_25 is not None and cash_25 is not None) else np.nan
    inv_cap_24 = (v(1, 'short_term_debt_total') + v(1, 'long_term_debt_noncurrent') + eq_24 - v(1, 'cash_and_short_term_investments'))
    avg_inv_cap = (inv_cap_25 + inv_cap_24) / 2 if (inv_cap_25 and inv_cap_24) else inv_cap_25

    metrics["m11_roe"] = ni_roe_25 / avg_eq if avg_eq else np.nan
    metrics["m12_roa"] = ni_roe_25 / avg_assets if avg_assets else np.nan
    metrics["m13_roic"] = nopat_25 / avg_inv_cap if avg_inv_cap else np.nan
    metrics["m14_asset_turnover"] = rev_25 / avg_assets if avg_assets else np.nan

    # 4. Funding & Reinvestment
    delta_re = re_25 - re_24 if (re_25 is not None and re_24 is not None) else np.nan
    retention_ratio = delta_re / ni_roe_25 if ni_roe_25 else np.nan
    metrics["m15_sustainable_growth_rate"] = metrics["m11_roe"] * retention_ratio if (metrics["m11_roe"] and retention_ratio) else np.nan

    net_capex = ppe_25 - ppe_24 if (ppe_25 is not None and ppe_24 is not None) else np.nan
    metrics["m16_reinvestment_rate"] = net_capex / nopat_25 if (net_capex is not None and nopat_25) else np.nan
    metrics["m17_free_cash_flow"] = (ni_25 + da_25 - net_capex) if (ni_25 is not None and da_25 is not None and net_capex is not None) else np.nan
    metrics["m18_capex_to_sales"] = net_capex / rev_25 if (net_capex is not None and rev_25) else np.nan

    # 5. Leverage & Liquidity
    total_debt = std_25 + ltd_25 if (std_25 is not None and ltd_25 is not None) else np.nan
    ebitda_25 = ebit_25 + da_25 if (ebit_25 is not None and da_25 is not None) else np.nan

    metrics["m19_debt_to_equity"] = total_debt / eq_25 if (total_debt is not None and eq_25) else np.nan
    metrics["m20_debt_to_assets"] = total_debt / assets_25 if (total_debt is not None and assets_25) else np.nan
    metrics["m21_net_debt_to_ebitda"] = (total_debt - cash_25) / ebitda_25 if (total_debt is not None and cash_25 is not None and ebitda_25) else np.nan
    metrics["m22_current_ratio"] = ca_25 / cl_25 if cl_25 else np.nan
    metrics["m23_quick_ratio"] = (ca_25 - inv_25) / cl_25 if (ca_25 is not None and inv_25 is not None and cl_25) else np.nan
    metrics["m24_cash_ratio"] = cash_25 / cl_25 if cl_25 else np.nan
    metrics["m25_interest_coverage"] = ebit_25 / int_exp_25 if int_exp_25 else np.nan
    return metrics


def run_fundamental_extraction(db, tickers: list[str] | None = None):
    # 1. Ensure the fundamental_metrics table exists (with the period and currency columns)
    ensure_table(db)

    # 2. SEC statements via OpenBB (full currency units, reporting currency on every row).
    inserted = 0
    with db.conn.cursor() as cur:
        for ticker in tickers or configured_tickers():
            stm = load_company_statements(ticker)
            if stm is None:
                continue  # not an SEC filer (non-US names are out of scope for now) or fetch failed
            if stm.is_financial:
                logger.warning("%s is a bank/insurer: industrial ratios are not meaningful, skipped", ticker)
                continue
            if len(stm.years) < 2:
                logger.warning("%s: fewer than 2 fiscal years available, skipped", ticker)
                continue
            metrics = compute_growth_metrics(stm)

            # 3. Insert into DB
            for metric_id, value in metrics.items():
                if pd.isna(value): continue
                
                upsert_metric(cur, ticker, metric_id, value, get_grade(metric_id, value), stm)
                inserted += 1
                
        db.conn.commit()
    
    logger.info("Calculated and stored %d fundamental metrics.", inserted)
    return {"metrics_calculated": inserted}