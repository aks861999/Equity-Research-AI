"""
Everything that actually touches the database, the CSVs (indirectly, via
the already-populated fundamental_metrics table) or the internet lives here,
kept separate from the PydanticAI tool wrappers in screening_agent.py so it
can be unit tested without spinning up an agent at all.

Design choice on "two tools only": the task constrains the AGENT to exactly
two tools (a news tool and a fundamentals tool). It does not forbid those
two tools from doing more than one query internally. So:

  - fundamentals_tool  -> calls `load_fundamentals_bundle()` below, which
    reads growth + risk metrics from Postgres (already computed offline by
    run_growth_fundamentals.py / run_risk_fundamentals.py / run_aggregation.py)
    and also derives the general (non-per-metric) sentiment signal.
  - news_tool          -> calls `load_news_bundle()` below, which fetches
    *current* Yahoo Finance news and SEC EDGAR filings live from the
    internet (per the task's literal requirement: "retrieves current news
    flow from the internet"), rather than only replaying whatever is
    already sitting in Postgres from a previous ingestion run.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import db_settings, get_company
from ..db.postgres_client import PostgresClient
from ..sources.sec_edgar import fetch_sec_filings
from ..sources.yahoo_finance import fetch_yahoo_news
from .metric_definitions import GROWTH_METRIC_DEFINITIONS, RISK_METRIC_DEFINITIONS
from .schemas import (
    FundamentalsToolOutput,
    GeneralSentimentSignal,
    GrowthMetric,
    NewsArticleItem,
    NewsToolOutput,
    RiskMetric,
    SecFilingItem,
)

logger = logging.getLogger("agent.data_access")


def get_db() -> PostgresClient:
    db = PostgresClient(**db_settings())
    db.ensure_db()
    return db


# ---------------------------------------------------------------------------
# Fundamentals tool support
# ---------------------------------------------------------------------------
def _fetch_fundamental_rows(db, ticker: str) -> dict[str, tuple[float | None, str]]:
    with db.conn.cursor() as cur:
        cur.execute(
            "SELECT metric_id, fundamental_value, fundamental_grade "
            "FROM fundamental_metrics WHERE ticker = %s",
            (ticker,),
        )
        rows = cur.fetchall()
    return {mid: (val, grade) for mid, val, grade in rows}


def _fetch_metric_sentiment_rows(db, ticker: str) -> dict[str, tuple[float, float, int]]:
    with db.conn.cursor() as cur:
        cur.execute(
            "SELECT metric_id, weighted_sentiment, dispersion, volume "
            "FROM metric_scores WHERE ticker = %s",
            (ticker,),
        )
        rows = cur.fetchall()
    return {mid: (ws, disp, vol) for mid, ws, disp, vol in rows}


def _fetch_general_article_sentiment(db, ticker: str, max_evidence: int = 8) -> GeneralSentimentSignal:
    """Aggregate sentiment across ALL scored articles for the ticker,
    regardless of which metric (if any) they were matched to, PLUS a short,
    per-article evidence list (title, date, sentiment) so the risk answer
    can cite specific news items rather than only a single averaged number.
    This is the company-level Yahoo-Finance-news signal used only for the
    risk question, since risk metrics have no per-metric sentiment."""
    with db.conn.cursor() as cur:
        cur.execute(
            """
            SELECT doc->>'title', doc->>'published_at',
                   doc->'sentiment'->>'score', doc->'sentiment'->>'label'
            FROM articles
            WHERE doc->>'ticker' = %s
              AND doc->>'source' = 'yahoo_finance'
              AND doc->'sentiment' IS NOT NULL
            ORDER BY doc->>'published_at' DESC
            """,
            (ticker,),
        )
        rows = cur.fetchall()

    scores = [float(r[2]) for r in rows if r[2] is not None]

    if not scores:
        return GeneralSentimentSignal(
            source="yahoo_finance_news",
            label="Unknown",
            summary="No scored Yahoo Finance articles were available for this ticker at run time.",
            article_or_filing_count=0,
            evidence=[],
        )

    avg = sum(scores) / len(scores)
    label = "Positive" if avg > 0.15 else "Negative" if avg < -0.15 else "Neutral"
    if len(scores) >= 3 and (max(scores) - min(scores)) > 1.0:
        label = "Mixed"
    summary = (
        f"Across {len(scores)} recent Yahoo Finance news items, average FinBERT sentiment is "
        f"{avg:+.2f} (scale roughly -1 very negative to +1 very positive), read as '{label}'. "
        f"This is a company-wide signal, not specific to any single risk metric."
    )

    evidence = []
    for title, published_at, score_str, sent_label in rows[:max_evidence]:
        if not title:
            continue
        date_part = (published_at or "")[:10] or "undated"
        score_part = f"{float(score_str):+.2f}" if score_str is not None else "n/a"
        evidence.append(f"[Yahoo Finance, {date_part}] \"{title}\" — sentiment {score_part} ({sent_label or 'n/a'})")

    return GeneralSentimentSignal(
        source="yahoo_finance_news", label=label, summary=summary,
        article_or_filing_count=len(scores), evidence=evidence,
    )


def _sec_filing_general_signal(filings: list[dict], max_evidence: int = 5) -> GeneralSentimentSignal:
    """SEC filings are regulatory/neutral in tone, so we do not run FinBERT
    on them; instead we describe disclosure activity/recency, which is what
    actually matters for the risk question (a burst of 8-K/6-K filings can
    itself be a risk signal, regardless of sentiment), plus a short
    per-filing content summary so the risk answer can cite what was
    actually disclosed rather than only a filing count."""
    if not filings:
        return GeneralSentimentSignal(
            source="sec_edgar_filings",
            label="Unknown",
            summary="No recent SEC EDGAR filings were retrieved for this ticker at run time "
                    "(this is expected for non-US-listed names with no sec_form_type configured).",
            article_or_filing_count=0,
            evidence=[],
        )
    form_types = sorted({f["form_type"] for f in filings})
    dates = sorted([f["filing_date"] for f in filings if f.get("filing_date")], reverse=True)
    most_recent = dates[0] if dates else "unknown date"
    summary = (
        f"{len(filings)} recent SEC filing section(s) retrieved (form types: {', '.join(form_types)}), "
        f"most recent dated {most_recent}. Filing volume/type is a disclosure-activity signal, not a "
        f"sentiment score; a sudden spike in material-event filings (e.g. 8-K) can itself indicate risk."
    )

    evidence = []
    for f in filings[:max_evidence]:
        snippet = _snippet(f.get("body_text"), max_chars=300)
        if not snippet:
            continue
        date_part = f.get("filing_date") or "undated"
        evidence.append(f"[SEC {f['form_type']}, {date_part}] {f['section']}: {snippet}")

    return GeneralSentimentSignal(
        source="sec_edgar_filings", label="Neutral", summary=summary,
        article_or_filing_count=len(filings), evidence=evidence,
    )


def load_fundamentals_bundle(db, ticker: str) -> FundamentalsToolOutput:
    """Self-contained: fetches its own SEC-filing signal live rather than
    depending on news_tool having run first or in a particular order (the
    two agent tools may be invoked by the model in either order, or the
    model could in principle call only one of them)."""
    fund_rows = _fetch_fundamental_rows(db, ticker)
    sentiment_rows = _fetch_metric_sentiment_rows(db, ticker)

    growth_metrics = []
    for metric_id, defn in GROWTH_METRIC_DEFINITIONS.items():
        val, grade = fund_rows.get(metric_id, (None, "N/A"))
        ws, disp, vol = sentiment_rows.get(metric_id, (None, None, None))
        growth_metrics.append(
            GrowthMetric(
                metric_id=metric_id,
                name=defn["name"],
                description=defn["description"],
                good_direction=defn["good_direction"],
                fundamental_value=val,
                fundamental_grade=grade,
                weighted_sentiment=ws,
                sentiment_dispersion=disp,
                sentiment_volume=vol,
            )
        )

    risk_metrics = []
    for metric_id, defn in RISK_METRIC_DEFINITIONS.items():
        val, grade = fund_rows.get(metric_id, (None, "N/A"))
        risk_metrics.append(
            RiskMetric(
                metric_id=metric_id,
                name=defn["name"],
                description=defn["description"],
                good_direction=defn["good_direction"],
                fundamental_value=val,
                fundamental_grade=grade,
            )
        )

    general_sentiment = [_fetch_general_article_sentiment(db, ticker)]

    company = get_company(ticker)
    sec_filings_for_signal: list[dict] = []
    if company.get("sec_form_type"):
        try:
            sec_filings_for_signal = fetch_sec_filings(
                ticker, company["sec_form_type"], max_results=5, lookback_days=180
            )
        except Exception as exc:
            logger.warning("SEC EDGAR fetch failed for %s (fundamentals bundle): %s", ticker, exc)
    general_sentiment.append(_sec_filing_general_signal(sec_filings_for_signal))

    return FundamentalsToolOutput(
        ticker=ticker,
        data_as_of=datetime.now(timezone.utc).isoformat(),
        growth_metrics=growth_metrics,
        risk_metrics=risk_metrics,
        general_sentiment=general_sentiment,
    )


# ---------------------------------------------------------------------------
# News tool support
# ---------------------------------------------------------------------------
def _snippet(text: str | None, max_chars: int = 500) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= max_chars else text[:max_chars].rsplit(" ", 1)[0] + "..."


def load_news_bundle(ticker: str, max_articles: int = 15, max_filings: int = 5) -> NewsToolOutput:
    company = get_company(ticker)

    try:
        raw_articles = fetch_yahoo_news(ticker, max_articles=max_articles)
    except Exception as exc:  # live network call: never let this crash the agent run
        logger.warning("Yahoo Finance news fetch failed for %s: %s", ticker, exc)
        raw_articles = []

    articles = [
        NewsArticleItem(
            title=a.get("title") or "(untitled)",
            source=a.get("provider") or "yahoo_finance",
            published_at=str(a.get("published_at")) if a.get("published_at") else None,
            snippet=_snippet(a.get("title")),  # yfinance headlines only; body_text lives in Postgres once extracted
        )
        for a in raw_articles
    ]

    raw_filings: list[dict] = []
    if company.get("sec_form_type"):
        try:
            raw_filings = fetch_sec_filings(
                ticker, company["sec_form_type"], max_results=max_filings, lookback_days=180
            )
        except Exception as exc:
            logger.warning("SEC EDGAR fetch failed for %s: %s", ticker, exc)
            raw_filings = []

    filings = [
        SecFilingItem(
            form_type=f["form_type"],
            filing_date=f.get("filing_date"),
            section=f["section"],
            snippet=_snippet(f.get("body_text")),
        )
        for f in raw_filings
    ]

    output = NewsToolOutput(
        ticker=ticker,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        recent_articles=articles,
        recent_sec_filings=filings,
    )
    return output