"""
Coverage report: what a company would get from the fundamentals pipeline, before it is added.

    python scripts/data-prep/coverage_report.py NEWTICKER OTHER      # any US tickers
    python scripts/data-prep/coverage_report.py                      # every ticker in companies.yaml
    python scripts/data-prep/coverage_report.py NEWTICKER --refresh  # ignore the 7-day cache

For each company it prints how many of the 25 growth and 11 risk ratios can be computed,
which ones are missing and which input is behind each gap, which inputs were filled from
raw SEC tags (and which tag), and any sanity-check warnings. No database is touched.
"""
from __future__ import annotations

import argparse
import logging

import numpy as np

from .inputs import METRIC_INPUTS
from .sec_fundamentals import configured_tickers, load_company_statements

_DEBT_FREE_UNDEFINED = {"m25_interest_coverage", "r08_interest_coverage", "r05_short_term_debt_ratio"}


def _company_metrics(stm) -> dict[str, float]:
    # Imported here: the runners import this package, so a top-level import would be circular.
    from ..extraction.run_growth_fundamentals import compute_growth_metrics
    from ..extraction.run_risk_fundamentals import compute_risk_metrics

    out = {}
    if len(stm.years) >= 2:
        out.update(compute_growth_metrics(stm))
    out.update(compute_risk_metrics(stm))
    return out


def _reason(stm, metric_id: str) -> str:
    missing = sorted({f for f, off in METRIC_INPUTS[metric_id] if np.isnan(stm.get(off, f))})
    if metric_id in _DEBT_FREE_UNDEFINED and "assumed_zero" in stm.sources().get("debt", ""):
        return "company has no debt, so the ratio is undefined"
    if missing:
        return "no " + ", ".join(missing) + " found in OpenBB or the raw SEC tags"
    return "undefined for these figures (zero base, or a loss in the growth base year)"


def report(tickers: list[str], refresh: bool = False) -> None:
    from ..agent.metric_definitions import GROWTH_METRIC_DEFINITIONS, RISK_METRIC_DEFINITIONS

    growth_ids, risk_ids = list(GROWTH_METRIC_DEFINITIONS), list(RISK_METRIC_DEFINITIONS)
    summary = []
    for ticker in tickers:
        print(f"\n=== {ticker} " + "=" * (60 - len(ticker)))
        stm = load_company_statements(ticker, refresh=refresh)
        if stm is None:
            print("  no data: not an SEC filer, or the fetch failed (see the log above)")
            summary.append((ticker, "no data", "", ""))
            continue
        if stm.is_financial:
            print("  bank or insurer: the industrial ratios do not apply, skipped by design")
            summary.append((ticker, "bank/insurer", "", ""))
            continue

        metrics = _company_metrics(stm)
        have = {m for m, val in metrics.items() if not np.isnan(val)}
        g = sum(m in have for m in growth_ids)
        r = sum(m in have for m in risk_ids)
        print(f"  FY{stm.years[0]} in {stm.currency}   growth {g}/{len(growth_ids)}   risk {r}/{len(risk_ids)}")
        for m in growth_ids + risk_ids:
            if m not in have:
                print(f"  - missing {m}: {_reason(stm, m)}")
        filled = stm.sources()
        if filled:
            print("  filled by fallback rules:")
            for field, how in sorted(filled.items()):
                print(f"      {field} <- {how}")
        for w in stm.warnings():
            print(f"  ! sanity check: {w}")
        summary.append((ticker, f"{g}/{len(growth_ids)}", f"{r}/{len(risk_ids)}", str(len(stm.warnings()))))

    print("\nSUMMARY")
    print(f"  {'ticker':8} {'growth':>8} {'risk':>8} {'warnings':>9}")
    for row in summary:
        print(f"  {row[0]:8} {row[1]:>8} {row[2]:>8} {row[3]:>9}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Report which fundamentals a company would get.")
    parser.add_argument("symbols", nargs="*", help="US tickers; default: every ticker in companies.yaml")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    report([s.upper() for s in args.symbols] or configured_tickers(), refresh=args.refresh)


if __name__ == "__main__":
    main()
