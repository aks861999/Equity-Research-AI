"""
Additional Requirement 3: Quality Evaluation.

These are deliberately simple, deterministic, non-LLM checks that can run
on every single production output with zero extra API cost or latency.
They catch the failure modes that matter most for a PM-facing screening
tool: missing/empty answers, a model sneaking in an investment
recommendation it isn't supposed to give, and numbers that don't trace
back to anything the tools actually returned (a lightweight groundedness /
hallucination check).

A heavier LLM-as-judge check is included as an optional add-on
(`llm_judge_check`) that can be run offline/periodically on a sample of
outputs — it is intentionally NOT part of the main agent's tool budget or
its "exactly two tools" constraint, since it is a separate, offline
evaluation call, not part of the screening agent itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schemas import FundamentalsToolOutput, ScreeningSummary

BANNED_DECISION_WORDS = re.compile(
    r"\b(buy|sell|strong buy|strong sell|hold rating|price target|overweight|underweight)\b",
    re.IGNORECASE,
)

ALLOWED_VERDICTS = {"Potentially relevant", "Not clearly relevant", "Insufficient data"}


@dataclass
class QualityReport:
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)

    @property
    def passed_all(self) -> bool:
        return all(self.checks.values())

    @property
    def score(self) -> float:
        return sum(self.checks.values()) / len(self.checks) if self.checks else 0.0

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks[name] = passed
        if detail:
            self.details[name] = detail


def _completeness_check(summary: ScreeningSummary, report: QualityReport) -> None:
    required_text_fields = [
        summary.growth_relevance_reasoning,
        summary.news_narrative_summary,
        summary.financial_highlights,
        summary.risk_concerns,
    ]
    non_empty = all(bool(f and f.strip()) for f in required_text_fields)
    report.add("completeness_all_four_answers_present", non_empty,
                "" if non_empty else "One or more of the four required answers is empty.")

    has_lists = bool(summary.key_positives) and bool(summary.key_negatives)
    report.add("completeness_positives_negatives_present", has_lists,
                "" if has_lists else "key_positives or key_negatives is empty.")


def _no_decision_language_check(summary: ScreeningSummary, report: QualityReport) -> None:
    full_text = " ".join([
        summary.growth_relevance_reasoning, summary.news_narrative_summary,
        summary.financial_highlights, summary.risk_concerns,
    ])
    match = BANNED_DECISION_WORDS.search(full_text)
    report.add("no_investment_decision_language", match is None,
                "" if match is None else f"Found banned phrase: '{match.group(0)}'.")

    valid_verdict = summary.growth_relevance_verdict in ALLOWED_VERDICTS
    report.add("verdict_is_from_allowed_set", valid_verdict,
                "" if valid_verdict else f"Unexpected verdict value: '{summary.growth_relevance_verdict}'.")


def _length_sanity_check(summary: ScreeningSummary, report: QualityReport) -> None:
    ok = all([
        20 <= len(summary.growth_relevance_reasoning) <= 1200,
        10 <= len(summary.news_narrative_summary) <= 800,
        20 <= len(summary.financial_highlights) <= 1200,
        20 <= len(summary.risk_concerns) <= 1200,
    ])
    report.add("answer_lengths_within_sane_bounds", ok,
                "" if ok else "One or more answers is implausibly short or long, worth a human look.")


def _groundedness_check(summary: ScreeningSummary, fundamentals: FundamentalsToolOutput, report: QualityReport) -> None:
    """Extract every number mentioned in the two fundamentals-heavy answers
    and check it is within a small tolerance of some fundamental_value the
    tool actually returned. This is a coarse hallucination check, not a
    proof of correctness -- it flags numbers that look invented, it doesn't
    guarantee every claim is right."""
    known_values = [
        m.fundamental_value for m in (fundamentals.growth_metrics + fundamentals.risk_metrics)
        if m.fundamental_value is not None
    ]
    # Percent figures are commonly quoted as e.g. "12%" for a 0.12 ratio.
    known_values_pct = [v * 100 for v in known_values]

    text = summary.financial_highlights + " " + summary.risk_concerns
    numbers_in_text = [float(n) for n in re.findall(r"-?\d+\.?\d*", text)]

    if not numbers_in_text:
        report.add("groundedness_numbers_traceable", True, "No numeric claims made; nothing to check.")
        return

    def _is_traceable(n: float) -> bool:
        pool = known_values + known_values_pct
        return any(abs(n - v) <= max(0.05, abs(v) * 0.05) for v in pool)

    traceable = [n for n in numbers_in_text if _is_traceable(n)]
    ratio = len(traceable) / len(numbers_in_text)
    report.add(
        "groundedness_numbers_traceable",
        ratio >= 0.6,
        f"{len(traceable)}/{len(numbers_in_text)} numeric mentions matched a supplied metric value "
        f"(ratio={ratio:.2f}); low ratios can indicate hallucinated figures.",
    )


def _metric_coverage_check(fundamentals: FundamentalsToolOutput, report: QualityReport) -> None:
    growth_available = sum(1 for m in fundamentals.growth_metrics if m.fundamental_value is not None)
    risk_available = sum(1 for m in fundamentals.risk_metrics if m.fundamental_value is not None)
    report.add(
        "sufficient_metric_coverage",
        growth_available >= 15 and risk_available >= 6,
        f"{growth_available}/25 growth metrics and {risk_available}/11 risk metrics had data; "
        f"screening confidence should be downgraded if coverage is low.",
    )


def evaluate_screening(summary: ScreeningSummary, fundamentals: FundamentalsToolOutput) -> QualityReport:
    report = QualityReport()
    _completeness_check(summary, report)
    _no_decision_language_check(summary, report)
    _length_sanity_check(summary, report)
    _groundedness_check(summary, fundamentals, report)
    _metric_coverage_check(fundamentals, report)
    return report


def llm_judge_check(summary: ScreeningSummary, model_call_fn) -> dict:
    """Optional, offline-only: ask a model to rate 1-5 whether the summary
    is a fair, concise reflection of PM-relevant screening information.
    `model_call_fn(prompt: str) -> str` is injected so this file has no
    hard dependency on any particular LLM client, and so it is trivial to
    mock in tests. NOT part of the two-tool agent's own run.
    """
    prompt = (
        "Rate 1-5 (5=excellent) how well this equity screening summary is grounded, concise, "
        "and free of investment recommendations. Reply with just the number.\n\n"
        f"{summary.model_dump_json(indent=2)}"
    )
    raw = model_call_fn(prompt).strip()
    try:
        score = int(re.findall(r"\d", raw)[0])
    except (IndexError, ValueError):
        score = None
    return {"llm_judge_score": score, "raw_response": raw}