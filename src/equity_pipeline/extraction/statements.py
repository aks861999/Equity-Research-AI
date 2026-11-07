"""
Loads the vendor statement CSVs with both statements in the same unit.

IncomeStatements.csv holds full currency units (e.g. 962195000.0), but
BalanceSheets.csv holds millions with an ' MM' suffix (e.g. '23494920.18 MM').
Any ratio that mixes the two (ROE, ROA, ROIC, net debt/EBITDA, capex-to-sales,
...) is off by a factor of 1,000,000 unless the balance sheet is converted, and
the wrong value can still earn a Green grade. The conversion lives here so every
calculator gets it, instead of each one repeating the CSV cleanup.
"""
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("statements")

BS_YEAR_COLUMNS = ["2025", "2024"]
MILLION = 1_000_000


def load_statements():
    """Returns (income_statement_df, balance_sheet_df) with every balance sheet
    value in full currency units, or None if the CSVs are missing."""
    root_dir = Path(__file__).resolve().parents[3]
    is_path = root_dir / "data" / "IncomeStatements.csv"
    bs_path = root_dir / "data" / "BalanceSheets.csv"

    if not is_path.exists() or not bs_path.exists():
        logger.error("Could not find CSV files in the data/ folder.")
        return None

    is_df = pd.read_csv(is_path)
    is_df.columns = is_df.columns.astype(str).str.strip()

    bs_df = pd.read_csv(bs_path)
    bs_df.columns = bs_df.columns.astype(str).str.strip()

    for col in BS_YEAR_COLUMNS:
        raw = bs_df[col].astype(str).str.strip()
        in_millions = raw.str.endswith(" MM")
        values = pd.to_numeric(raw.str.replace(" MM", "", regex=False), errors="coerce")
        # Only values tagged ' MM' are scaled; anything untagged is already in full units.
        bs_df[col] = values.where(~in_millions, values * MILLION)

    return is_df, bs_df
