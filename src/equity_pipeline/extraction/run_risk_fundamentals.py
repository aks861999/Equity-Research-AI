import os
import logging
import pandas as pd
import numpy as np
from pathlib import Path

logger = logging.getLogger("run_risk_fundamentals")

TICKER_MAP = {
    "MELI US Equity": "MELI",
    "5108 JP Equity": "5108.T",
    "GLBE US Equity": "GLBE",
    "ADS GR Equity": "ADS.DE"
}

def get_risk_grade(metric_id, value):
    """Applies Risk Thresholds (Red = High Risk, Green = Low Risk)."""
    if value is None or pd.isna(value):
        return "N/A"
    
    val = float(value)
    if metric_id == "r02_debt_to_equity":
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
    elif metric_id == "r12_risk_provision_trend":
        if val > 0.05: return "Red"
        elif val > 0.01: return "Yellow"
        else: return "Green"
    return "Ungraded"

def run_risk_fundamental_extraction(db):
    with db.conn.cursor() as cur:
        # Uses the SAME table as growth fundamentals!
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fundamental_metrics (
                id TEXT PRIMARY KEY,
                ticker TEXT,
                metric_id TEXT,
                fundamental_value FLOAT,
                fundamental_grade TEXT,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        db.conn.commit()

    root_dir = Path(__file__).resolve().parents[3]
    is_path = root_dir / "data" / "IncomeStatements.csv"
    bs_path = root_dir / "data" / "BalanceSheets.csv"
    
    if not is_path.exists() or not bs_path.exists():
        logger.error("Could not find CSV files in the data/ folder.")
        return

    is_df = pd.read_csv(is_path)
    is_df.columns = is_df.columns.astype(str).str.strip()
    bs_df = pd.read_csv(bs_path)
    bs_df.columns = bs_df.columns.astype(str).str.strip()
    
    for col in ['2025', '2024']:
        bs_df[col] = bs_df[col].astype(str).str.replace(' MM', '').str.strip()
        bs_df[col] = pd.to_numeric(bs_df[col], errors='coerce')

    inserted = 0
    with db.conn.cursor() as cur:
        for bb_ticker, ticker in TICKER_MAP.items():
            is_comp = is_df[is_df['BB_Ticker'] == bb_ticker]
            bs_comp = bs_df[bs_df['BB_Ticker'] == bb_ticker]
            
            if is_comp.empty or bs_comp.empty:
                continue

            def is_val(year, field):
                row = is_comp[is_comp['Year_'] == year]
                if row.empty: return np.nan
                val = row[field].values[0]
                return float(val) if pd.notna(val) else np.nan

            def bs_val(year, item):
                row = bs_comp[bs_comp['index'] == item]
                if row.empty: return np.nan
                return row[str(year)].values[0]

            # Data points
            rev_25 = is_val(2025, 'NET SALES OR REVENUES (1001)')
            ebit_25 = is_val(2025, 'EARNINGS BEFORE INTEREST AND TAXES (EBIT) (18191)')
            da_25 = is_val(2025, 'DEPRECIATION, DEPLETION AND AMORTIZATION (1151)')
            pretax_25 = is_val(2025, 'PRETAX INCOME (1401)')
            int_exp_25 = is_val(2025, 'INTEREST EXPENSE ON DEBT (1251)')
            extra_cred_25 = is_val(2025, 'EXTRAORDINARY CREDIT - PRETAX (1253)')
            extra_chg_25 = is_val(2025, 'EXTRAORDINARY CHARGE - PRETAX (1254)')

            assets_25 = bs_val(2025, 'Total Assets')
            eq_25 = bs_val(2025, 'Common Equity')
            total_liab_25 = bs_val(2025, 'Total Liabilities')
            std_25, ltd_25 = bs_val(2025, 'Short Term Debt'), bs_val(2025, 'Long Term Debt')
            cash_25 = bs_val(2025, 'Cash And Short Term Investments')
            ca_25, cl_25 = bs_val(2025, 'Current Assets'), bs_val(2025, 'Current Liabilities')
            inv_25 = bs_val(2025, 'Inventories')
            re_25 = bs_val(2025, 'Retained Earnings')
            intangibles_25 = bs_val(2025, 'Intangible Investments')
            risk_prov_25 = bs_val(2025, 'Prov For Risk Charges')

            # Calculate 12 Risk Metrics
            metrics = {}
            total_debt = std_25 + ltd_25 if (std_25 is not None and ltd_25 is not None) else np.nan
            ebitda_25 = ebit_25 + da_25 if (ebit_25 is not None and da_25 is not None) else np.nan
            
            # 1. Altman Z-Score requires Market Cap, which we don't have. We skip it to avoid bad data.
            # metrics["r01_altman_z_score"] = ... 
            
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
            metrics["r12_risk_provision_trend"] = risk_prov_25 / total_liab_25 if (risk_prov_25 is not None and total_liab_25) else np.nan

            for metric_id, value in metrics.items():
                if pd.isna(value): continue
                
                grade = get_risk_grade(metric_id, value)
                doc_id = f"{ticker}:{metric_id}"
                
                cur.execute("""
                    INSERT INTO fundamental_metrics (id, ticker, metric_id, fundamental_value, fundamental_grade, calculated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE 
                    SET fundamental_value = EXCLUDED.fundamental_value,
                        fundamental_grade = EXCLUDED.fundamental_grade;
                """, (doc_id, ticker, metric_id, round(float(value), 4), grade, pd.Timestamp.now()))
                inserted += 1
                
        db.conn.commit()
    
    logger.info("Calculated and stored %d RISK fundamental metrics.", inserted)
    return {"risk_metrics_calculated": inserted}