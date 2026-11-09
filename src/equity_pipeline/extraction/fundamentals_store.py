"""
The `fundamental_metrics` table, shared by the growth and risk runners.

Besides the ratio, every row records WHICH statements it was computed from, so the screening
agent can say so instead of guessing:

    fiscal_year_end  end date of the latest annual statement, e.g. '2025-12-31'
    period_type      'annual' (the ratios use the 10-K / 20-F, never a quarter)
    currency         reporting currency of that statement, e.g. 'USD'

Older databases get the three columns added automatically (ALTER TABLE ... IF NOT EXISTS).
"""
from __future__ import annotations

import pandas as pd

PERIOD_TYPE = "annual"

_DDL = """
    CREATE TABLE IF NOT EXISTS fundamental_metrics (
        id TEXT PRIMARY KEY,
        ticker TEXT,
        metric_id TEXT,
        fundamental_value FLOAT,
        fundamental_grade TEXT,
        calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        fiscal_year_end TEXT,
        period_type TEXT,
        currency TEXT
    );
"""


def ensure_table(db) -> None:
    with db.conn.cursor() as cur:
        cur.execute(_DDL)
        for column in ("fiscal_year_end TEXT", "period_type TEXT", "currency TEXT"):
            cur.execute(f"ALTER TABLE fundamental_metrics ADD COLUMN IF NOT EXISTS {column}")
        db.conn.commit()


def upsert_metric(cur, ticker: str, metric_id: str, value: float, grade: str, stm) -> None:
    """One ratio of one company; `stm` is its CompanyStatements (period and currency come from it)."""
    cur.execute("""
        INSERT INTO fundamental_metrics
            (id, ticker, metric_id, fundamental_value, fundamental_grade, calculated_at,
             fiscal_year_end, period_type, currency)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE
        SET fundamental_value = EXCLUDED.fundamental_value,
            fundamental_grade = EXCLUDED.fundamental_grade,
            calculated_at     = EXCLUDED.calculated_at,
            fiscal_year_end   = EXCLUDED.fiscal_year_end,
            period_type       = EXCLUDED.period_type,
            currency          = EXCLUDED.currency;
    """, (f"{ticker}:{metric_id}", ticker, metric_id, round(float(value), 4), grade, pd.Timestamp.now(),
          stm.period_ending, PERIOD_TYPE, stm.currency))
