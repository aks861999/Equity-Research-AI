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

from ..extraction.grading import RELATIVE_GRADED
from .schemas import FundamentalsToolOutput, ScreeningSummary

# Whole words only, and not inside a hyphenated word ("sell-side", "buy-side" are fine).
BANNED_DECISION_WORDS = re.compile(
    r"(?<![\w-])(buy|sell|strong buy|strong sell|hold rating|price target|overweight|underweight)(?![\w-])",
    re.IGNORECASE,
)
# A quoted headline or analyst phrase ("... Is It a Buy Now?") is the news being reported, not the
# model's own recommendation. Strip quoted spans before checking. A single quote only opens a span
# when it is not inside a word, so apostrophes ("Fool's") are untouched.
_QUOTED_SPAN = re.compile(r"\"[^\"]{4,}\"|“[^”]{4,}”|(?<![A-Za-z0-9])'[^']{10,}?'(?![A-Za-z0-9])")

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
    match = BANNED_DECISION_WORDS.search(_QUOTED_SPAN.sub(" ", full_text))
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


def _all_strings(obj) -> list[str]:
    """Every string inside a nested dict / list / pydantic model (the news tool's payload)."""
    if obj is None:
        return []
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _all_strings(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _all_strings(v)]
    return []


def _evidence_text(fundamentals: FundamentalsToolOutput, news=None) -> str:
    """All the news / filing text the model was given. The news_tool output is only here when passed
    as `news`; otherwise only what the fundamentals tool carried (Yahoo items, SEC description)."""
    return " ".join(
        [f"{g.summary} {' '.join(g.evidence)}" for g in fundamentals.general_sentiment]
        + _all_strings(news)  # the news tool's headlines, article leads and filing excerpts
    )


def _evidence_numbers(fundamentals: FundamentalsToolOutput, news=None) -> list[float]:
    """Every number in the supplied news / filing evidence (an 8-K's revenue, a headline's score)."""
    return [_to_float(n) for n in re.findall(_NUMBER, _evidence_text(fundamentals, news))]


def _groundedness_check(summary: ScreeningSummary, fundamentals: FundamentalsToolOutput, report: QualityReport,
                        news=None) -> None:
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

    evidence_numbers = _evidence_numbers(fundamentals, news)

    text = summary.financial_highlights + " " + summary.risk_concerns
    numbers_in_text = [
        v for v in (_to_float(n) for n in re.findall(_NUMBER, text))
        if not _is_trivial_number(v)
    ]

    if not numbers_in_text:
        report.add("groundedness_numbers_traceable", True, "No numeric claims made; nothing to check.")
        return

    def _is_traceable(n: float) -> bool:
        pool = known_values + known_values_pct + evidence_numbers
        return any(abs(n - v) <= max(0.05, abs(v) * 0.05) for v in pool)

    traceable = [n for n in numbers_in_text if _is_traceable(n)]
    ratio = len(traceable) / len(numbers_in_text)
    report.add(
        "groundedness_numbers_traceable",
        ratio >= 0.6,
        f"{len(traceable)}/{len(numbers_in_text)} numeric mentions matched a supplied metric value or "
        f"evidence figure (ratio={ratio:.2f}); low ratios can indicate hallucinated figures. "
        f"Years and small counts are ignored.",
    )


_NUMBER = r"-?\d[\d,]*\.?\d*"


def _to_float(raw: str) -> float:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return float("nan")


def _is_trivial_number(v: float) -> bool:
    """Years (1990-2100) and small whole counts (quarter numbers, "2 red flags") are not claims
    about a figure, so they neither prove nor disprove groundedness."""
    return v != v or (v == int(v) and (abs(v) <= 12 or 1990 <= abs(v) <= 2100))


def _metric_coverage_check(fundamentals: FundamentalsToolOutput, report: QualityReport) -> None:
    growth_available = sum(1 for m in fundamentals.growth_metrics if m.fundamental_value is not None)
    risk_available = sum(1 for m in fundamentals.risk_metrics if m.fundamental_value is not None)
    report.add(
        "sufficient_metric_coverage",
        growth_available >= 15 and risk_available >= 6,
        f"{growth_available}/25 growth metrics and {risk_available}/11 risk metrics had data; "
        f"screening confidence should be downgraded if coverage is low.",
    )


# ---------------------------------------------------------------------------
# Metric-bound checks: does the brief say the RIGHT thing about each metric?
#
# The groundedness check above only asks "does this number appear somewhere in the data". These ask
# per metric: a number written next to a metric's name must be that metric's own value (or its
# sentiment figures), a metric with no value must get no number, and a Red/Green grade must not be
# described with the opposite wording. Deterministic, no LLM, no cost.
#
# They are deliberately conservative: a number is only checked when it clearly belongs to one metric
# name (it follows the name closely, or sits right in front of it). Anything ambiguous is skipped,
# so a pass means "nothing wrong found", not "everything verified".
# ---------------------------------------------------------------------------
# (phrase pattern, metric id prefixes). The phrase names the metric; some phrases fit a growth AND a risk
# metric with the same value (current ratio, debt-to-equity, ...). Free cash flow is left out: it is an
# absolute amount, not a ratio, and amounts are never checked here.
_METRIC_PHRASES: list[tuple[str, tuple[str, ...]]] = [
    (r"revenue growth|sales growth|top-line growth|revenue(?=\s?[+\-−]\d)", ("m01",)),
    (r"operating (?:profit|income) growth|ebit growth|operating profit(?!\s+margin)", ("m02",)),
    (r"net (?:income|profit) growth|net income(?!\s+margin)", ("m03",)),
    (r"(?:total )?asset growth|balance[- ]sheet growth", ("m04",)),
    (r"gross margin", ("m05",)),
    (r"operating (?:profit )?margin", ("m06",)),
    (r"ebit margin", ("m07",)),
    (r"ebitda margin", ("m08",)),
    (r"net (?:profit |income )?margin", ("m09",)),
    (r"sg&a", ("m10",)),
    (r"roe|return on equity", ("m11",)),
    (r"roa|return on assets", ("m12",)),
    (r"roic|return on invested capital", ("m13",)),
    (r"asset turnover", ("m14",)),
    (r"sustainable growth(?: rate)?", ("m15",)),
    (r"reinvestment rate", ("m16",)),
    (r"capex|capital expenditures?", ("m18",)),
    (r"debt[- ]to[- ]equity|debt/equity|d/e", ("m19", "r02")),
    (r"debt[- ]to[- ]assets|debt/assets", ("m20",)),
    (r"net debt(?:[- ]to[- ]|/)ebitda", ("m21", "r04")),
    (r"current ratio", ("m22", "r06")),
    (r"quick ratio", ("m23", "r07")),
    (r"cash ratio", ("m24",)),
    (r"interest coverage", ("m25", "r08")),
    (r"altman", ("r01",)),
    (r"debt ratio", ("r03",)),
    (r"short[- ]term debt(?: ratio)?", ("r05",)),
    (r"retained earnings", ("r09",)),
    (r"non-recurring(?: items)?|extraordinary items", ("r10",)),
    (r"goodwill(?: (?:and|&) intangibles)?", ("r11",)),
]
_PHRASES = [(re.compile(rf"(?<![A-Za-z])(?:{p})(?![A-Za-z])", re.IGNORECASE), ids) for p, ids in _METRIC_PHRASES]

_CLAIM_NUMBER = re.compile(r"(?<![\w.])([+\-−]?)(\d[\d,]*(?:\.\d+)?)")
_AMOUNT_SUFFIX = re.compile(r"\s?(?:[BMKT]\b|bn\b|m\b|k\b|billion|million|trillion|thousand)")
_IGNORED_TOKENS = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}-[A-Za-z]\b")   # dates, "8-K", "10-K"
_SENTENCE_BREAK = re.compile(r"[.;!?](?:\s|$)|\n")
_CLAUSE_END = re.compile(r"\.(?!\d)|[;\n]|,\s")     # where the words about one metric stop (not a decimal point)
_TIGHT_GAP = 10       # a number this close after a metric name belongs to it ("gross margin 48.8%")
_FAR_GAP = 40         # ... or up to this far, if no other number or sentence break sits between
_POSITIVE_WORDS = re.compile(r"\b(strong|healthy|robust|excellent|solid|favou?rable|comfortabl[ey]|improved|"
                             r"impressive|strength)\b", re.IGNORECASE)
_NEGATIVE_WORDS = re.compile(r"\b(weak|poor|concerning|worrying|deteriorat\w*|stretched|risky|alarming|"
                             r"troubling|dangerous)\b", re.IGNORECASE)
_CLAUSE_SKIP = re.compile(r"\b(despite|although|though|but|while|whereas|however|yet|sentiment|news|articles?|"
                          r"headlines?)\b", re.IGNORECASE)   # the wording may be about something else


def _metric_values(fundamentals: FundamentalsToolOutput) -> dict[str, dict]:
    """id prefix ('m01', 'r03') -> {'values': numbers the brief may quote for it, 'grade': its grade}."""
    out: dict[str, dict] = {}
    for m in fundamentals.growth_metrics + fundamentals.risk_metrics:
        values = [getattr(m, k, None) for k in
                  ("fundamental_value", "weighted_sentiment", "sentiment_dispersion", "sentiment_volume")]
        out[m.metric_id.split("_")[0]] = {
            "values": [float(v) for v in values if v is not None],
            "has_value": m.fundamental_value is not None,
            "grade": (m.fundamental_grade or "").lower(),
            "relative": m.metric_id in RELATIVE_GRADED,
        }
    return out


def _phrase_hits(text: str, known: dict[str, dict]) -> list[tuple[int, int, str, tuple[str, ...]]]:
    """Metric names found in `text`, left to right, longest first where two overlap
    ('short-term debt ratio' wins over the 'debt ratio' inside it). Only metrics the tools returned."""
    hits = []
    for pattern, ids in _PHRASES:
        ids = tuple(i for i in ids if i in known)
        if ids:
            hits += [(m.start(), m.end(), m.group(0), ids) for m in pattern.finditer(text)]
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    kept: list = []
    for h in hits:
        if not kept or h[0] >= kept[-1][1]:
            kept.append(h)
    return kept


def _near(value: float, sign: str, candidates: list[float]) -> bool:
    """`value` is the quoted magnitude, `sign` the quoted '+', '-' or ''. A ratio may be quoted as a
    fraction (0.107) or as a percent (10.7%)."""
    for c in candidates:
        for scaled in (c, c * 100):
            if abs(value - abs(scaled)) > max(0.01, abs(scaled) * 0.05):
                continue
            if (sign == "-" and scaled > 0) or (sign == "+" and scaled < 0):
                continue
            return True
    return False


def _owner_of_number(text: str, start: int, end: int, hits: list) -> tuple | None:
    """The metric name a number belongs to, or None when that is not clear.
    A number follows its metric name ("gross margin 48.8%"), or sits right in front of it
    ("a 3.1% reinvestment rate"). Another number or a sentence break in between means: not clear."""
    before = [h for h in hits if h[1] <= start]
    prior = before[-1] if before else None
    gap = text[prior[1]:start] if prior else ""
    prior_ok = bool(prior) and len(gap) <= _FAR_GAP and not re.search(r"\d", gap) and not _SENTENCE_BREAK.search(gap)
    if prior_ok and len(gap) <= _TIGHT_GAP:
        return prior
    after = next((h for h in hits if h[0] >= end), None)
    if after and re.fullmatch(r"%?\s{0,2}", text[end:after[0]]):
        return after
    return prior if prior_ok else None


def _brief_fields(summary: ScreeningSummary) -> list[str]:
    return [summary.growth_relevance_reasoning, summary.news_narrative_summary, summary.financial_highlights,
            summary.risk_concerns, *summary.key_positives, *summary.key_negatives]


def _metric_bound_checks(summary: ScreeningSummary, fundamentals: FundamentalsToolOutput, report: QualityReport,
                         news=None) -> None:
    known = _metric_values(fundamentals)
    evidence = _evidence_numbers(fundamentals, news)
    checked = 0
    wrong: list[str] = []      # a number that is not that metric's own value
    invented: list[str] = []   # a number for a metric the tools returned no value for
    grade_clash: list[str] = []

    for field_text in _brief_fields(summary):
        text = _IGNORED_TOKENS.sub(lambda m: " " * len(m.group(0)), field_text or "")
        hits = _phrase_hits(text, known)
        if not hits:
            continue

        for m in _CLAIM_NUMBER.finditer(text):
            sign = {"−": "-"}.get(m.group(1), m.group(1))
            value = _to_float(m.group(2))
            if value != value or _is_trivial_number(value):
                continue
            if (m.start() > 0 and text[m.start() - 1] in "$€£") or _AMOUNT_SUFFIX.match(text[m.end():]):
                continue   # an amount of money, not a ratio
            if re.match(r"\s?[-–]\s?\d", text[m.end():]) or re.search(r"\d\s?[-–]$", text[:m.start()]):
                continue   # a range such as "45-46%" (guidance), not one metric value
            owner = _owner_of_number(text, m.start(), m.end(), hits)
            if owner is None:
                continue
            candidates = [v for i in owner[3] for v in known[i]["values"]]
            checked += 1
            in_evidence = any(abs(value - abs(e)) <= max(0.01, abs(e) * 0.05) for e in evidence if e == e)
            if _near(value, sign, candidates) or in_evidence:
                continue
            shown = f"'{owner[2]}' {sign}{m.group(2)}"
            if candidates:
                wrong.append(f"{shown} (data: {', '.join(f'{v:g}' for v in candidates[:2])})")
            else:
                invented.append(f"{shown} (no value supplied)")

        # a Red metric described as strong, or a Green one as weak, inside its own clause
        for k, h in enumerate(hits):
            clause_end = _CLAUSE_END.search(text, h[1])
            end = clause_end.start() if clause_end else len(text)
            clause = text[h[0]:end]
            if _CLAUSE_SKIP.search(clause) or any(o[0] < end for o in hits[k + 1:]):
                continue
            if any(known[i]["relative"] for i in h[3]):
                continue   # a trend or relative grade says nothing about whether the LEVEL is strong or weak
            grades = {known[i]["grade"] for i in h[3]}
            if len(grades) != 1:
                continue
            grade = grades.pop()
            word = (_POSITIVE_WORDS if grade == "red" else _NEGATIVE_WORDS if grade == "green" else None)
            found = word.search(clause) if word else None
            if found:
                grade_clash.append(f"'{h[2]}' is {grade.title()} but described as '{found.group(0)}'")

    report.add("numbers_match_their_metric", not wrong,
               f"{checked} number(s) next to a metric name checked; "
               + ("all matched." if not wrong else "not matching: " + "; ".join(wrong[:3]) + "."))
    report.add("no_numbers_for_missing_metrics", not invented,
               "no number given for a metric without a value." if not invented
               else "a figure was written for a metric the tools returned no value for: " + "; ".join(invented[:3]) + ".")
    report.add("grade_wording_matches_grade", not grade_clash,
               "no Red metric called strong and no Green metric called weak." if not grade_clash
               else "; ".join(grade_clash[:3]) + ".")


def _risk_flag_check(summary: ScreeningSummary, fundamentals: FundamentalsToolOutput, report: QualityReport) -> None:
    """`risk_flag_count` must equal the number of risk metrics graded Red in the data."""
    actual = sum(1 for m in fundamentals.risk_metrics if (m.fundamental_grade or "").lower() == "red")
    report.add("risk_flag_count_matches_grades", summary.risk_flag_count == actual,
               f"risk_flag_count is {summary.risk_flag_count}; {actual} risk metric(s) are graded Red in the data.")


# Words that explain a figure or describe a trend over time. The ratios are one annual snapshot and
# the tools carry no such explanation, so these words are only fine when the news or filing text the
# model was given uses them too (for example an article that says margins are "accelerating").
_EXPLANATION_WORDS = re.compile(
    r"\b(decelerat\w*|accelerat\w*|flatter\w*|buy-?backs?|repurchas\w*|trailing|adjusted basis)\b", re.IGNORECASE)


def _unsupported_explanation_check(summary: ScreeningSummary, fundamentals: FundamentalsToolOutput,
                                   report: QualityReport, news=None) -> None:
    """Flags a brief that explains a figure or describes a trend with words found nowhere in the
    supplied data. A coarse check by word stem: it cannot judge a claim, only a missing source."""
    evidence = _evidence_text(fundamentals, news).lower().replace("-", "")
    text = _QUOTED_SPAN.sub(" ", " ".join(_brief_fields(summary)))
    unsupported = sorted({m.group(0).lower() for m in _EXPLANATION_WORDS.finditer(text)
                          if m.group(0).lower().replace("-", "")[:6] not in evidence})
    report.add("no_unsupported_explanations", not unsupported,
               "no trend or explanation word without a source in the supplied data." if not unsupported
               else "words with no source in the supplied data: " + ", ".join(unsupported)
               + " (the ratios are one annual snapshot; the tools carry no trend or cause).")


def evaluate_screening(summary: ScreeningSummary, fundamentals: FundamentalsToolOutput,
                       news=None) -> QualityReport:
    """`news`: what the news tool returned (the `news_payload` of run_screening's result, or a
    NewsToolOutput). Without it, figures quoted from filings or article leads look untraceable."""
    report = QualityReport()
    _completeness_check(summary, report)
    _no_decision_language_check(summary, report)
    _length_sanity_check(summary, report)
    _groundedness_check(summary, fundamentals, report, news=news)
    _metric_coverage_check(fundamentals, report)
    _metric_bound_checks(summary, fundamentals, report, news=news)
    _risk_flag_check(summary, fundamentals, report)
    _unsupported_explanation_check(summary, fundamentals, report, news=news)
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