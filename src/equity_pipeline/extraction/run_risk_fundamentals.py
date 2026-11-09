import os
import logging
import pandas as pd
import numpy as np
from pathlib import Path

from ..sources.sec_fundamentals import configured_tickers, load_company_statements
from .fundamentals_store import ensure_table, upsert_metric

logger = logging.getLogger("run_risk_fundamentals")

def get_risk_grade(metric_id, value):
    """Applies Risk Thresholds (Red = High Risk, Green = Low Risk)."""
    if value is None or pd.isna(value):
        return "N/A"
    
    val = float(value)
    if metric_id == "r01_altman_z_score":
        # Altman Z'' (non-manufacturer version): safe above 2.6, distress below 1.1.
        # Unlike the other risk metrics, HIGHER is safer.
        if val < 1.1: return "Red"
        elif val < 2.6: return "Yellow"
        else: return "Green"
    elif metric_id == "r02_debt_to_equity":
        if val > 2.0: return "Red"
        elif val > 1.0: return "Yellow"
        else: return "Green"
    elif metric_id == "r03_debt_ratio":
        if val > 0.5: return "Red"
        elif val > 0.3: return "Yellow"
        else: return "Green"
    elif metric_id == "r04_net_debt_to_ebitda":
        if val > 4.0: return "Red"
        elif val > 2.0: return "Yellow"
        else: return "Green"
    elif metric_id == "r05_short_term_debt_ratio":
        if val > 0.5: return "Red"
        elif val > 0.3: return "Yellow"
        else: return "Green"
    elif metric_id == "r06_current_ratio":
        if val < 1.0: return "Red"
        elif val < 1.5: return "Yellow"
        else: return "Green"
    elif metric_id == "r07_quick_ratio":
        if val < 0.5: return "Red"
        elif val < 1.0: return "Yellow"
        else: return "Green"
    elif metric_id == "r08_interest_coverage":
        if val < 1.5: return "Red"
        elif val < 3.0: return "Yellow"
        else: return "Green"
    elif metric_id == "r09_retained_earnings_deficit":
        if val < 0: return "Red"
        elif val < 0.2: return "Yellow"
        else: return "Green"
    elif metric_id == "r10_non_recurring_items":
        if val > 0.05: return "Red"
        elif val > 0: return "Yellow"
        else: return "Green"
    elif metric_id == "r11_goodwill_intangibles":
        if val > 0.4: return "Red"
        elif val > 0.2: return "Yellow"
        else: return "Green"
    return "Ungraded"

def compute_risk_metrics(stm) -> dict[str, float]:
    """The 11 risk ratios for one company (NaN where an input is missing).
    Pure function of the SEC statements: no database, so the coverage report can reuse it."""
    v = stm.get  # v(0, openbb_field) = latest fiscal year; suffix _25 below means "latest"

    # Data points
    ebit_25 = v(0, 'ebit')
    da_25 = v(0, 'depreciation_and_amortization')
    pretax_25 = v(0, 'total_pretax_income')
    int_exp_25 = v(0, 'interest_expense')
    extra_cred_25 = v(0, 'extraordinary_credit')
    extra_chg_25 = v(0, 'extraordinary_charge')

    assets_25 = v(0, 'total_assets')
    eq_25 = v(0, 'total_common_equity')
    total_liab_25 = v(0, 'total_liabilities')
    std_25, ltd_25 = v(0, 'short_term_debt_total'), v(0, 'long_term_debt_noncurrent')
    cash_25 = v(0, 'cash_and_short_term_investments')
    ca_25, cl_25 = v(0, 'total_current_assets'), v(0, 'total_current_liabilities')
    inv_25 = v(0, 'inventory')
    re_25 = v(0, 'retained_earnings')
    intangibles_25 = v(0, 'intangibles_total')

    # Calculate 11 Risk Metrics
    metrics = {}
    total_debt = std_25 + ltd_25 if (std_25 is not None and ltd_25 is not None) else np.nan
    ebitda_25 = ebit_25 + da_25 if (ebit_25 is not None and da_25 is not None) else np.nan

    # 1. Altman Z'' (non-manufacturer version, used for every company). It replaces the
    #    original's market-cap term with book equity, so it needs no price data:
    #    Z'' = 6.56*WC/TA + 3.26*RE/TA + 6.72*EBIT/TA + 1.05*BookEquity/TotalLiabilities
    #    Missing inputs are NaN and propagate, so no value is stored.
    if assets_25 and total_liab_25:
        metrics["r01_altman_z_score"] = (
            6.56 * (ca_25 - cl_25) / assets_25
            + 3.26 * re_25 / assets_25
            + 6.72 * ebit_25 / assets_25
            + 1.05 * eq_25 / total_liab_25
        )

    metrics["r02_debt_to_equity"] = total_debt / eq_25 if (total_debt is not None and eq_25) else np.nan
    metrics["r03_debt_ratio"] = total_liab_25 / assets_25 if (total_liab_25 is not None and assets_25) else np.nan
    metrics["r04_net_debt_to_ebitda"] = (total_debt - cash_25) / ebitda_25 if (total_debt is not None and cash_25 is not None and ebitda_25) else np.nan
    metrics["r05_short_term_debt_ratio"] = std_25 / total_debt if (std_25 is not None and total_debt and total_debt > 0) else np.nan

    metrics["r06_current_ratio"] = ca_25 / cl_25 if cl_25 else np.nan
    metrics["r07_quick_ratio"] = (ca_25 - inv_25) / cl_25 if (ca_25 is not None and inv_25 is not None and cl_25) else np.nan
    metrics["r08_interest_coverage"] = ebit_25 / int_exp_25 if int_exp_25 else np.nan

    metrics["r09_retained_earnings_deficit"] = re_25 / assets_25 if (re_25 is not None and assets_25) else np.nan

    extra_items = (abs(extra_cred_25 or 0) + abs(extra_chg_25 or 0))
    metrics["r10_non_recurring_items"] = extra_items / abs(pretax_25) if (pretax_25 and abs(pretax_25) > 0) else np.nan

    metrics["r11_goodwill_intangibles"] = intangibles_25 / assets_25 if (intangibles_25 is not None and assets_25) else np.nan
    return metrics


def run_risk_fundamental_extraction(db, tickers: list[str] | None = None):
    ensure_table(db)  # the SAME table as the growth fundamentals

    inserted = 0
    with db.conn.cursor() as cur:
        for ticker in tickers or configured_tickers():
            stm = load_company_statements(ticker)
            if stm is None:
                continue  # not an SEC filer (non-US names are out of scope for now) or fetch failed
            if stm.is_financial:
                logger.warning("%s is a bank/insurer: industrial ratios are not meaningful, skipped", ticker)
                continue
            metrics = compute_risk_metrics(stm)

            for metric_id, value in metrics.items():
                if pd.isna(value): continue
                
                upsert_metric(cur, ticker, metric_id, value, get_risk_grade(metric_id, value), stm)
                inserted += 1
                
        db.conn.commit()
    
    logger.info("Calculated and stored %d RISK fundamental metrics.", inserted)
    return {"risk_metrics_calculated": inserted}
