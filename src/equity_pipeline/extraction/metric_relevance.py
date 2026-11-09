"""
How strongly an article is about one metric, judged from its full text (not the headline).

The news search keeps up to 10 articles per metric without filtering on keywords, because a
relevant article does not always name the metric in its title or snippet. Relevance is decided
here, once the body text has been downloaded, and used as a weight in the aggregation:

    metric phrase in the title or the opening (first LEDE_CHARS)  -> 1.0
    metric phrase later in the body                               -> 0.6
    no metric phrase anywhere                                     -> 0.2
    no body text (should not reach the aggregation)               -> 0.6

The phrases are the quoted phrases of the metric's template in config/metrics.yaml (any of them,
whole words, ignoring case). Same idea as the company relevance in sentiment.py (company name in
the title 1.0, in the opening 0.8, elsewhere 0.2), applied per metric. Nothing is deleted: an
off-topic article only counts little.
"""
from __future__ import annotations

import re

LEDE_CHARS = 1000  # same "opening" as the company relevance check in sentiment.py

WEIGHT_TITLE_OR_LEDE = 1.0
WEIGHT_IN_BODY = 0.6
WEIGHT_ABSENT = 0.2
WEIGHT_NO_BODY = 0.6

_PHRASE = re.compile(r'"([^"]+)"')


def metric_phrases(search_template: str) -> list[str]:
    """All quoted phrases of a metric's template ("liquidity", "working capital", ...)."""
    return _PHRASE.findall(search_template or "")


def mentions_any(text: str | None, phrases: list[str] | None) -> bool:
    """True when `text` contains one of the phrases as whole words, ignoring case
    ("ROE" matches "ROE" but not "heroes")."""
    if not text or not phrases:
        return False
    return any(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", text, re.IGNORECASE) for p in phrases)


def metric_relevance(title: str | None, body_text: str | None, phrases: list[str] | None) -> float:
    """Weight of one (article, metric) pair, see the module docstring. No phrases = 1.0 (no judgement)."""
    if not phrases:
        return 1.0
    if not body_text:
        return WEIGHT_NO_BODY
    if mentions_any(title, phrases) or mentions_any(body_text[:LEDE_CHARS], phrases):
        return WEIGHT_TITLE_OR_LEDE
    if mentions_any(body_text, phrases):
        return WEIGHT_IN_BODY
    return WEIGHT_ABSENT


def phrases_by_metric(metrics: list[dict]) -> dict[str, list[str]]:
    """metric_id -> its phrases, from the loaded config/metrics.yaml entries."""
    return {m["metric_id"]: metric_phrases(m.get("search_template", "")) for m in metrics}
