import os
import logging
import pandas as pd
import numpy as np
from pathlib import Path

logger = logging.getLogger("run_fundamentals")

# Map Bloomberg Tickers to our internal tickers
TICKER_MAP = {
    "MELI US Equity": "MELI",
    "5108 JP Equity": "5108.T",
    "GLBE US Equity": "GLBE",
    "ADS GR Equity": "ADS.DE"
}

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

def run_fundamental_extraction(db):
    # 1. Ensure the fundamental_metrics table exists
    with db.conn.cursor() as cur:
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

    # 2. Load CSVs
    root_dir = Path(__file__).resolve().parents[3]
    is_path = root_dir / "data" / "IncomeStatements.csv"
    bs_path = root_dir / "data" / "BalanceSheets.csv"
    
    if not is_path.exists() or not bs_path.exists():
        logger.error("Could not find CSV files in the data/ folder.")
        return

    # Read IS (skip first row which is just '0 | index | Year_ ...')
    # Read IS
    is_df = pd.read_csv(is_path)
    is_df.columns = is_df.columns.astype(str).str.strip()
    
    # Read BS (handle the ' MM' suffix)
    bs_df = pd.read_csv(bs_path)
    bs_df.columns = bs_df.columns.astype(str).str.strip()
    
    # ---> ADD THIS DEBUG LINE <---
    print("\nDEBUG: Columns found in BalanceSheets.csv:")
    print(bs_df.columns.tolist())
    print("\n")
    
    # Clean BS numeric columns (remove ' MM' and convert to float)
    for col in ['2025', '2024']:
        bs_df[col] = bs_df[col].astype(str).str.replace(' MM', '').str.strip()
        bs_df[col] = pd.to_numeric(bs_df[col], errors='coerce')

    inserted = 0
    with db.conn.cursor() as cur:
        for bb_ticker, ticker in TICKER_MAP.items():
            is_comp = is_df[is_df['BB_Ticker'] == bb_ticker]
            bs_comp = bs_df[bs_df['BB_Ticker'] == bb_ticker]
            
            if is_comp.empty or bs_comp.empty:
                logger.warning("Missing data for %s", ticker)
                continue

            # Helper to get IS values
            def is_val(year, field):
                row = is_comp[is_comp['Year_'] == year]
                if row.empty: return np.nan
                val = row[field].values[0]
                return float(val) if pd.notna(val) else np.nan

            # Helper to get BS values
            def bs_val(year, item):
                row = bs_comp[bs_comp['index'] == item]
                if row.empty: return np.nan
                return row[str(year)].values[0]

            # IS Data points
            rev_23, rev_24, rev_25 = is_val(2023, 'NET SALES OR REVENUES (1001)'), is_val(2024, 'NET SALES OR REVENUES (1001)'), is_val(2025, 'NET SALES OR REVENUES (1001)')
            ebit_25 = is_val(2025, 'EARNINGS BEFORE INTEREST AND TAXES (EBIT) (18191)')
            da_25 = is_val(2025, 'DEPRECIATION, DEPLETION AND AMORTIZATION (1151)')
            gross_inc_25 = is_val(2025, 'GROSS INCOME (1100)')
            op_inc_25 = is_val(2025, 'OPERATING INCOME (1250)')
            sga_25 = is_val(2025, 'SELLING, GENERAL & ADMINISTRATIVE EXPENSES (1101)')
            ni_25 = is_val(2025, 'NET INCOME BEFORE EXTRA ITEMS/PREFERRED DIVIDENDS (1551)') # Using 1551 for margins
            ni_roe_25 = is_val(2025, 'NET INCOME USED TO CALCULATE BASIC EPS (1706)')       # Using 1706 for ROE
            pretax_25 = is_val(2025, 'PRETAX INCOME (1401)')
            tax_25 = is_val(2025, 'INCOME TAXES (1451)')
            int_exp_25 = is_val(2025, 'INTEREST EXPENSE ON DEBT (1251)')

            # BS Data points
            assets_24, assets_25 = bs_val(2024, 'Total Assets'), bs_val(2025, 'Total Assets')
            eq_24, eq_25 = bs_val(2024, 'Common Equity'), bs_val(2025, 'Common Equity')
            std_25, ltd_25 = bs_val(2025, 'Short Term Debt'), bs_val(2025, 'Long Term Debt')
            cash_25 = bs_val(2025, 'Cash And Short Term Investments')
            ca_25, cl_25 = bs_val(2025, 'Current Assets'), bs_val(2025, 'Current Liabilities')
            inv_25 = bs_val(2025, 'Inventories')
            re_24, re_25 = bs_val(2024, 'Retained Earnings'), bs_val(2025, 'Retained Earnings')
            ppe_24, ppe_25 = bs_val(2024, 'Gross PPE'), bs_val(2025, 'Gross PPE')

            # Calculate 25 Metrics
            metrics = {}
            # 1. Growth (Using 2-year CAGR where 2023 data exists, as per Part 1.1)
            metrics["m01_revenue_growth"] = (rev_25 / rev_23)**0.5 - 1 if (rev_23 and rev_23 > 0) else (rev_25 - rev_24) / rev_24 if rev_24 else np.nan
            metrics["m02_operating_profit_growth"] = (ebit_25 / is_val(2023, 'EARNINGS BEFORE INTEREST AND TAXES (EBIT) (18191)'))**0.5 - 1 if (is_val(2023, 'EARNINGS BEFORE INTEREST AND TAXES (EBIT) (18191)') and is_val(2023, 'EARNINGS BEFORE INTEREST AND TAXES (EBIT) (18191)') > 0) else np.nan
            metrics["m03_net_income_growth"] = (ni_25 / is_val(2023, 'NET INCOME BEFORE EXTRA ITEMS/PREFERRED DIVIDENDS (1551)'))**0.5 - 1 if (is_val(2023, 'NET INCOME BEFORE EXTRA ITEMS/PREFERRED DIVIDENDS (1551)') and is_val(2023, 'NET INCOME BEFORE EXTRA ITEMS/PREFERRED DIVIDENDS (1551)') > 0) else np.nan
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
            inv_cap_24 = (bs_val(2024, 'Short Term Debt') + bs_val(2024, 'Long Term Debt') + eq_24 - bs_val(2024, 'Cash And Short Term Investments')) if (eq_24 is not None) else np.nan
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

            # 5. Insert into DB
            for metric_id, value in metrics.items():
                if pd.isna(value): continue
                
                grade = get_grade(metric_id, value)
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
    
    logger.info("Calculated and stored %d fundamental metrics.", inserted)
    return {"metrics_calculated": inserted}