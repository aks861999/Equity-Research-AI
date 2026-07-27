"""
All Pydantic models shared between the two agent tools and the agent's
final structured output. Keeping these in one file means the tool outputs
and the final-answer schema can be type-checked against each other, and
means evaluation.py (quality checks) can import the same definitions used
to build the prompt in the first place.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Tool 1 output: fundamentals_tool
# ---------------------------------------------------------------------------
class GrowthMetric(BaseModel):
    metric_id: str
    name: str
    description: str
    good_direction: str
    fundamental_value: float | None = Field(
        None, description="The computed financial ratio/figure from BalanceSheets.csv / IncomeStatements.csv."
    )
    fundamental_grade: str = Field(
        "N/A", description="Red/Yellow/Green/Ungraded/N/A threshold grade, see run_growth_fundamentals.get_grade()."
    )
    weighted_sentiment: float | None = Field(
        None, description="Time-decayed, relevance-weighted FinBERT sentiment score for news matched to this "
                           "specific metric, range roughly -1 (very negative) to +1 (very positive). None if no "
                           "matched articles were scored for this metric."
    )
    sentiment_dispersion: float | None = Field(
        None, description="Standard deviation of the raw per-article sentiment scores behind weighted_sentiment. "
                           "High dispersion means the news on this metric is contradictory/mixed rather than "
                           "one-sided; low dispersion means the news agrees."
    )
    sentiment_volume: int | None = Field(
        None, description="Number of distinct scored articles behind weighted_sentiment/dispersion. Low volume "
                           "means the sentiment figure rests on very little evidence and should be trusted less."
    )


class RiskMetric(BaseModel):
    metric_id: str
    name: str
    description: str
    good_direction: str
    fundamental_value: float | None = None
    fundamental_grade: str = "N/A"
    # Deliberately NO per-metric sentiment fields: risk-theme metrics were
    # never run through the Google News per-metric pipeline, so there is no
    # weighted_sentiment/dispersion/volume for these. Do not backfill with
    # fake zeros — None is the honest value and the agent is instructed to
    # treat it as "no news signal available" rather than "neutral".


class GeneralSentimentSignal(BaseModel):
    """Company-level (not metric-specific) sentiment context used only for
    the risk question, since no per-metric risk sentiment exists."""
    source: str = Field(..., description="'yahoo_finance_news' or 'sec_edgar_filings'.")
    label: str = Field(..., description="One of: Positive, Neutral, Negative, Mixed, Unknown.")
    summary: str = Field(..., description="Short, source-grounded textual description written for the risk question.")
    article_or_filing_count: int = 0
    evidence: list[str] = Field(
        default_factory=list,
        description="Short, individual per-article or per-filing summaries (title/date/score or "
                     "title/date/snippet) backing `summary` above, so the risk answer can cite specifics "
                     "instead of only an aggregate number.",
    )


class FundamentalsToolOutput(BaseModel):
    ticker: str
    data_as_of: str
    growth_metrics: list[GrowthMetric]
    risk_metrics: list[RiskMetric]
    general_sentiment: list[GeneralSentimentSignal] = Field(
        default_factory=list,
        description="Company-level sentiment from yfinance/SEC EDGAR, for the risk question only.",
    )


# ---------------------------------------------------------------------------
# Tool 2 output: news_tool
# ---------------------------------------------------------------------------
class NewsArticleItem(BaseModel):
    title: str
    source: str
    published_at: str | None = None
    snippet: str = Field(..., description="Short excerpt/summary of the article body, used to ground positives/negatives.")


class SecFilingItem(BaseModel):
    form_type: str
    filing_date: str | None = None
    section: str
    snippet: str


class NewsToolOutput(BaseModel):
    ticker: str
    fetched_at: str
    recent_articles: list[NewsArticleItem]
    recent_sec_filings: list[SecFilingItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Final structured output the agent must produce (one call = one screening)
# ---------------------------------------------------------------------------
class ScreeningSummary(BaseModel):
    ticker: str

    # Q1
    growth_relevance_verdict: str = Field(
        ..., description="One of: 'Potentially relevant', 'Not clearly relevant', 'Insufficient data'. "
                          "Never a buy/sell/hold call."
    )
    growth_relevance_reasoning: str = Field(
        ..., description="2-4 sentences reasoning over the 25 fundamental growth metrics AND their matched "
                          "weighted_sentiment/dispersion/volume, explaining why the company does or does not "
                          "look like a growth-strategy candidate right now."
    )

    # Q2
    key_positives: list[str] = Field(..., description="Top positive drivers from recent news/filings, max 5.")
    key_negatives: list[str] = Field(..., description="Top negative drivers/risks from recent news/filings, max 5.")
    news_narrative_summary: str = Field(..., description="2-3 sentence plain-English summary of the current news narrative.")

    # Q3
    financial_highlights: str = Field(
        ..., description="High-level takeaway on growth, profitability, capital efficiency, and leverage/liquidity, "
                          "grounded only in the fundamental_value/grade figures actually supplied."
    )

    # Q4
    risk_concerns: str = Field(
        ..., description="Obvious risks/concerns grounded in the 11 risk metrics plus the general "
                          "yfinance/SEC-EDGAR sentiment signals. Must explicitly flag where risk metrics are "
                          "missing/N-A rather than silently ignoring gaps."
    )
    risk_flag_count: int = Field(..., description="Count of risk metrics graded Red across the risk_metrics list.")

    disclaimer: str = Field(
        default="This is an initial screening output for early-stage evaluation only, not an investment "
                "recommendation or final decision.",
        description="Always echoed back unchanged so the UI can display it verbatim.",
    )