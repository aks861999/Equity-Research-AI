"""
CLI entry point.

Usage:
    python run_screening.py --ticker MELI
    python run_screening.py --ticker GLBE --seed 7

Requires the same environment/.env setup as the rest of equity_pipeline
(Postgres reachable, MANAGED_LLM_API_KEY set) plus `pip install pydantic-ai`.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from equity_pipeline.agent.data_access import get_db, load_fundamentals_bundle  # noqa: E402
from equity_pipeline.agent.evaluation import evaluate_screening  # noqa: E402
from equity_pipeline.agent.reproducibility import DEFAULT_SEED  # noqa: E402
from equity_pipeline.agent.screening_agent import run_screening  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("run_screening_cli")


def main():
    parser = argparse.ArgumentParser(description="Run the investment-screening agent for one ticker.")
    parser.add_argument("--ticker", required=True, help="Ticker as configured in config/companies.yaml, e.g. MELI")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--no-refresh", action="store_true",
                         help="Skip the live pipeline (SEC EDGAR/Yahoo/Google News ingest + FinBERT + "
                              "aggregation + fundamentals) and just read whatever is already in Postgres.")
    parser.add_argument("--no-google-news", action="store_true",
                         help="Run the live pipeline but skip the slow Google News per-metric step "
                              "(growth-metric sentiment will reflect the last time it was run, if ever).")
    args = parser.parse_args()

    run = run_screening(
        args.ticker,
        seed=args.seed,
        refresh_data=not args.no_refresh,
        include_google_news=not args.no_google_news,
    )
    summary = run["result"]

    # Re-pull the fundamentals bundle once more (cheap DB read, no LLM/tool call)
    # purely so the offline quality checks have the same data to check against.
    db = get_db()
    fundamentals = load_fundamentals_bundle(db, args.ticker.upper())
    quality = evaluate_screening(summary, fundamentals)

    print("\n" + "=" * 70)
    print(f"SCREENING SUMMARY: {summary.ticker}")
    print("=" * 70)
    print(f"\n[Q1] Growth relevance: {summary.growth_relevance_verdict}")
    print(summary.growth_relevance_reasoning)
    print(f"\n[Q2] Key positives:")
    for p in summary.key_positives:
        print(f"  + {p}")
    print("Key negatives:")
    for n in summary.key_negatives:
        print(f"  - {n}")
    print(f"\nNews narrative: {summary.news_narrative_summary}")
    print(f"\n[Q3] Financial highlights:\n{summary.financial_highlights}")
    print(f"\n[Q4] Risk concerns (red flags: {summary.risk_flag_count}):\n{summary.risk_concerns}")
    print(f"\n{summary.disclaimer}")

    print("\n" + "-" * 70)
    print("MONITORING")
    print("-" * 70)
    print(json.dumps(run["monitoring"], indent=2, default=str))

    if run["pipeline_counts"] is not None:
        print("\n" + "-" * 70)
        print("LIVE PIPELINE (SEC EDGAR / Yahoo / Google News / FinBERT / fundamentals)")
        print("-" * 70)
        print(json.dumps(run["pipeline_counts"], indent=2, default=str))

    print("\n" + "-" * 70)
    print(f"QUALITY EVALUATION (score={quality.score:.2f}, passed_all={quality.passed_all})")
    print("-" * 70)
    for name, passed in quality.checks.items():
        flag = "PASS" if passed else "FAIL"
        detail = f" — {quality.details[name]}" if name in quality.details else ""
        print(f"  [{flag}] {name}{detail}")

    print(f"\nRun manifest saved to: {run['manifest_path']}")


if __name__ == "__main__":
    main()
