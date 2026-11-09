"""
Everything that actually touches the database (including the SEC-derived
fundamental_metrics table) or the internet lives here,
kept separate from the PydanticAI tool wrappers in screening_agent.py so it
can be unit tested without spinning up an agent at all.

Design choice on "two tools only": the task constrains the AGENT to exactly
two tools (a news tool and a fundamentals tool). It does not forbid those
two tools from doing more than one query internally. So:

  - fundamentals_tool  -> calls `load_fundamentals_bundle()` below, which
    reads growth + risk metrics from Postgres (already computed offline by
    run_growth_fundamentals.py / run_risk_fundamentals.py / run_aggregation.py)
    and also derives the general (non-per-metric) sentiment signal.
  - news_tool          -> calls `load_news_bundle()` below, which reads the newest stored
    Yahoo Finance headlines (with the text lead of the first few) and the newest stored
    SEC EDGAR filing excerpts from Postgres.

Neither tool makes a network call. The live part ("current news flow from the internet") is the
refresh step, `pipeline_runner.run_live_pipeline`, which fetches SEC EDGAR and Yahoo Finance and
stores what is new just BEFORE the agent runs. The tools then read that, so the brief and the
dashboard's sources table show the same data, and a run with the refresh switched off uses
whatever was stored last.
"""
from __future__ import annotations
import pandas as pd
import logging
from datetime import datetime, timedelta, timezone

from ..config import db_settings, get_company
from ..extraction.quality_gate import is_about_company
from ..db.postgres_client import PostgresClient
from .metric_definitions import GROWTH_METRIC_DEFINITIONS, RISK_METRIC_DEFINITIONS
from .schemas import (
    FundamentalsPeriod,
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



def _query_df(db, query: str, params: tuple) -> pd.DataFrame:
    """Query result as a DataFrame via the cursor (pandas' read_sql_query warns about psycopg2 connections)."""
    with db.conn.cursor() as cur:
        cur.execute(query, params)
        columns = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


def fetch_fundamental_metrics_df(db, ticker: str) -> pd.DataFrame:
    """Raw fundamental_metrics rows for a ticker, for UI/debug display."""
    return _query_df(
        db,
        "SELECT metric_id, fundamental_value, fundamental_grade, calculated_at, fiscal_year_end, currency "
        "FROM fundamental_metrics WHERE ticker = %s ORDER BY metric_id",
        (ticker,),
    )


def fetch_metric_scores_df(db, ticker: str) -> pd.DataFrame:
    """Raw metric_scores rows for a ticker, for UI/debug display."""
    return _query_df(
        db,
        "SELECT metric_id, weighted_sentiment, dispersion, volume "
        "FROM metric_scores WHERE ticker = %s ORDER BY metric_id",
        (ticker,),
    )


SOURCE_LABELS = {"searxng_news": "News (search)", "yahoo_finance": "News (Yahoo Finance)",
                 "google_news": "News (Google, old)"}


def sec_filings_page_url(ticker: str) -> str:
    """SEC EDGAR page listing the company's filings: the origin of the financial statements."""
    return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=10-K&owner=include&count=40"


def fetch_reference_sources(db, ticker: str) -> pd.DataFrame:
    """Every article and SEC filing stored for a ticker, with its link, for the dashboard's
    "Reference sources" table. Reads link metadata only (never the article text).

    Columns: Type, Title, Link, Published, Date from, Metrics, Sentiment, Score, Text. News rows
    that contributed to a sentiment score come first (newest first), then the rest."""
    query = """
        SELECT doc->>'type', doc->>'source', doc->>'title', doc->>'url', doc->>'source_url',
               doc->>'form_type', doc->>'section', doc->>'filing_date', doc->>'published_at',
               doc->>'date_source', doc->'matched_metrics', doc->'sentiment'->>'label',
               doc->'sentiment'->>'score', doc->'extraction'->>'status', doc->'extraction'->>'reject_reason'
        FROM articles
        WHERE doc->>'ticker' = %s AND doc->>'type' IN ('raw_article', 'sec_filing')
    """
    with db.conn.cursor() as cur:
        cur.execute(query, (ticker,))
        rows = cur.fetchall()

    records = []
    for (kind, source, title, url, source_url, form_type, section, filing_date, published,
         date_source, matched, label, score, extraction, reject_reason) in rows:
        if kind == "sec_filing":
            records.append({
                "Type": f"SEC filing {form_type or ''}".strip(), "Title": f"{form_type or 'Filing'} - {section or ''}".strip(" -"),
                "Link": source_url, "Published": (filing_date or "")[:10], "Date from": "filing",
                "Metrics": "", "Sentiment": "", "Score": None, "Text": "stored", "_scored": 0,
            })
            continue
        metrics = ", ".join(sorted({m.get("metric_id", "").split("_")[0] for m in (matched or []) if m.get("metric_id")}))
        records.append({
            "Type": SOURCE_LABELS.get(source, source or "News"), "Title": title, "Link": url,
            "Published": (published or "")[:10], "Date from": date_source or "unknown", "Metrics": metrics,
            "Sentiment": label or "", "Score": round(float(score), 3) if score not in (None, "") else None,
            "Text": (f"rejected: {reject_reason}" if extraction == "rejected" else extraction) or "",
            "_scored": 1 if label else 0,
        })

    df = pd.DataFrame(records, columns=["Type", "Title", "Link", "Published", "Date from", "Metrics",
                                         "Sentiment", "Score", "Text", "_scored"])
    if df.empty:
        return df.drop(columns="_scored")
    df = df.sort_values(["_scored", "Published"], ascending=[False, False]).reset_index(drop=True)
    return df.drop(columns="_scored")


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


def fetch_data_freshness(db, ticker: str) -> dict[str, str | None]:
    """When the prepared (offline) data behind a ticker was last refreshed, as 'YYYY-MM-DD' or None:

      news_last_updated    the latest time a search-news article was found again (the SearXNG backend run)
      scores_last_updated  the latest per-metric sentiment aggregation
      ratios_last_updated  the latest fundamental ratio calculation
      fiscal_year_end      the annual statement those ratios come from

    The online dashboard shows these so a visitor knows which parts are live and which are prepared."""
    def one(sql: str) -> str | None:
        try:
            with db.conn.cursor() as cur:
                cur.execute(sql, (ticker,))
                row = cur.fetchone()
        except Exception:  # a table or column that does not exist yet
            return None
        return str(row[0])[:10] if row and row[0] else None

    return {
        "news_last_updated": one(
            "SELECT max(doc->>'last_seen_at') FROM articles "
            "WHERE doc->>'type' = 'raw_article' AND doc->>'source' = 'searxng_news' AND doc->>'ticker' = %s"),
        "scores_last_updated": one("SELECT max(calculated_at) FROM metric_scores WHERE ticker = %s"),
        "ratios_last_updated": one("SELECT max(calculated_at) FROM fundamental_metrics WHERE ticker = %s"),
        "fiscal_year_end": one("SELECT max(fiscal_year_end) FROM fundamental_metrics WHERE ticker = %s"),
    }


def _fetch_period(db, ticker: str) -> FundamentalsPeriod | None:
    """Fiscal year-end, period type and currency the stored ratios were computed from (None for
    rows stored before these columns existed: re-run scripts/data-prep/run_fundamentals.py)."""
    try:
        with db.conn.cursor() as cur:
            cur.execute(
                "SELECT fiscal_year_end, period_type, currency FROM fundamental_metrics "
                "WHERE ticker = %s AND fiscal_year_end IS NOT NULL LIMIT 1", (ticker,))
            row = cur.fetchone()
    except Exception:  # an old table without the columns
        logger.warning("fundamental_metrics has no period columns yet; run scripts/data-prep/run_fundamentals.py")
        return None
    if not row:
        return None
    fiscal_year_end, period_type, currency = row
    return FundamentalsPeriod(fiscal_year_end=fiscal_year_end, period_type=period_type or "annual", currency=currency)


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
                   doc->'sentiment'->>'score', doc->'sentiment'->>'label',
                   doc->>'body_text'
            FROM articles
            WHERE doc->>'ticker' = %s
              AND doc->>'source' = 'yahoo_finance'
              AND jsonb_typeof(doc->'sentiment') = 'object'
            ORDER BY doc->>'published_at' DESC
            """,
            (ticker,),
        )
        scored_rows = [r for r in cur.fetchall() if r[2] is not None]

    # Yahoo's ticker feed also carries pieces that are not about the company (an Uber CEO story that
    # names it once in passing, a hedge-fund profile). Only items ABOUT the company enter the average
    # and the evidence (see quality_gate.is_about_company).
    name, symbol = _company_name(ticker), ticker
    rows = [r for r in scored_rows if is_about_company(r[0], r[4], name, symbol)]
    left_out = len(scored_rows) - len(rows)
    left_out_note = (f" {left_out} further scored item(s) were left out because they are not about the "
                     f"company (it is not in the title and is named only in passing)." if left_out else "")
    scores = [float(r[2]) for r in rows]

    if not scores:
        return GeneralSentimentSignal(
            source="yahoo_finance_news",
            label="Unknown",
            summary="No scored Yahoo Finance articles about this company were available at run time."
                    + left_out_note,
            article_or_filing_count=0,
            evidence=[],
        )

    avg = sum(scores) / len(scores)
    label = "Positive" if avg > 0.15 else "Negative" if avg < -0.15 else "Neutral"
    if len(scores) >= 3 and (max(scores) - min(scores)) > 1.0:
        label = "Mixed"
    summary = (
        f"Across {len(scores)} recent Yahoo Finance news items about the company, average FinBERT "
        f"sentiment is {avg:+.2f} (scale roughly -1 very negative to +1 very positive), read as "
        f"'{label}'. This is a company-wide signal, not specific to any single risk metric."
        + left_out_note
    )

    evidence = []
    for title, published_at, score_str, sent_label, _lead in rows[:max_evidence]:
        if not title:
            continue
        date_part = (published_at or "")[:10] or "undated"
        score_part = f"{float(score_str):+.2f}" if score_str is not None else "n/a"
        evidence.append(f"[Yahoo Finance, {date_part}] \"{title}\" — sentiment {score_part} ({sent_label or 'n/a'})")

    return GeneralSentimentSignal(
        source="yahoo_finance_news", label=label, summary=summary,
        article_or_filing_count=len(scores), evidence=evidence,
    )


SEC_SIGNAL_LOOKBACK_DAYS = 180


def _sec_filing_general_signal(filings: list[dict]) -> GeneralSentimentSignal:
    """SEC filings are regulatory/neutral in tone, so we do not run FinBERT on them; this describes
    disclosure activity and recency, which is what matters for the risk question (a burst of 8-K/6-K
    filings can itself be a risk signal, regardless of sentiment).

    It carries NO excerpts: the filing texts reach the model once, through news_tool
    (recent_sec_filings). `filings` are metadata rows (form_type, filing_date) read from the database,
    so building this signal needs no download."""
    if not filings:
        return GeneralSentimentSignal(
            source="sec_edgar_filings",
            label="Unknown",
            summary="No SEC EDGAR filings are stored for this ticker in the last "
                    f"{SEC_SIGNAL_LOOKBACK_DAYS} days (expected for non-US-listed names with no "
                    "sec_form_type configured).",
            article_or_filing_count=0,
            evidence=[],
        )
    form_types = sorted({f["form_type"] for f in filings})
    dates = sorted([f["filing_date"] for f in filings if f.get("filing_date")], reverse=True)
    most_recent = dates[0] if dates else "unknown date"
    summary = (
        f"{len(filings)} SEC filing section(s) in the last {SEC_SIGNAL_LOOKBACK_DAYS} days (form types: "
        f"{', '.join(form_types)}), most recent dated {most_recent}. Filing volume/type is a "
        f"disclosure-activity signal, not a sentiment score; a sudden spike in material-event filings "
        f"(e.g. 8-K) can itself indicate risk. The filing excerpts are in news_tool.recent_sec_filings."
    )
    return GeneralSentimentSignal(
        source="sec_edgar_filings", label="Neutral", summary=summary,
        article_or_filing_count=len(filings), evidence=[],
    )


def _fetch_recent_filing_rows(db, ticker: str, days: int = SEC_SIGNAL_LOOKBACK_DAYS) -> list[dict]:
    """(form_type, filing_date) of the SEC filing sections stored for a ticker in the last `days` days."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    with db.conn.cursor() as cur:
        cur.execute(
            "SELECT doc->>'form_type', doc->>'filing_date' FROM articles "
            "WHERE doc->>'type' = 'sec_filing' AND doc->>'ticker' = %s AND doc->>'filing_date' >= %s "
            "ORDER BY doc->>'filing_date' DESC", (ticker, since))
        return [{"form_type": f, "filing_date": d} for f, d in cur.fetchall()]


def load_fundamentals_bundle(db, ticker: str) -> FundamentalsToolOutput:
    """Self-contained: reads everything it needs from the database (ratios, per-metric sentiment, the
    Yahoo signal and the SEC filing activity), so it does not depend on news_tool having run first or
    in a particular order (the model may call the two tools in either order, or in principle only one).
    It makes no network call."""
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

    # Disclosure activity from the stored filings (no download). The excerpts themselves are sent
    # once, by news_tool, so the model does not receive the same filing text twice.
    general_sentiment.append(_sec_filing_general_signal(_fetch_recent_filing_rows(db, ticker)))

    return FundamentalsToolOutput(
        ticker=ticker,
        data_as_of=datetime.now(timezone.utc).isoformat(),
        period=_fetch_period(db, ticker),
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


def _company_name(ticker: str) -> str | None:
    try:
        return get_company(ticker)["primary_search_name"]
    except (ValueError, KeyError):
        return None


def load_news_bundle(ticker: str, db, max_articles: int = 15, max_filings: int = 5,
                     max_text_articles: int = 5, text_chars: int = 900,
                     filings_lookback_days: int = SEC_SIGNAL_LOOKBACK_DAYS) -> NewsToolOutput:
    """What news_tool hands to the model. It makes NO network call: it reads what the refresh step
    (run_live_pipeline) stored in the database just before the agent runs, so SEC EDGAR and Yahoo
    Finance are fetched once per run, and the model sees exactly what the sources table shows.
    With the refresh switched off it shows whatever was stored last.

      * the `max_articles` newest stored Yahoo Finance headlines. Each is checked for being ABOUT the
        company (`about_company`: named in the title, or repeatedly in its stored text). The first
        `max_text_articles` that are about the company and have stored text carry the lead of that text
        (about `text_chars` characters); every other headline carries its title only, and headlines
        that are not about the company are marked so (market-wide pieces).
      * up to `max_filings` stored SEC filing sections of the last `filings_lookback_days` days, newest
        first, with excerpts. This is the ONLY place the filing texts go to the model (the fundamentals
        tool carries just their metadata).
    """
    company_name = _company_name(ticker)

    with db.conn.cursor() as cur:
        cur.execute(
            "SELECT doc->>'title', doc->>'published_at', doc->'extraction'->>'status', doc->>'body_text' "
            "FROM articles WHERE doc->>'type' = 'raw_article' AND doc->>'ticker' = %s "
            "AND doc->>'source' = 'yahoo_finance' "
            "ORDER BY doc->>'published_at' DESC NULLS LAST LIMIT %s",
            (ticker, max_articles))
        yahoo_rows = cur.fetchall()

        since = (datetime.now(timezone.utc) - timedelta(days=filings_lookback_days)).strftime("%Y-%m-%d")
        cur.execute(
            "SELECT doc->>'form_type', doc->>'filing_date', doc->>'section', "
            "left(coalesce(doc->>'body_text', ''), 1000) "
            "FROM articles WHERE doc->>'type' = 'sec_filing' AND doc->>'ticker' = %s "
            "AND doc->>'filing_date' >= %s "
            "ORDER BY doc->>'filing_date' DESC, id LIMIT %s",
            (ticker, since, max_filings))
        filing_rows = cur.fetchall()

    articles = []
    text_slots = max_text_articles
    for title, published_at, status, body_text in yahoo_rows:
        title = title or "(untitled)"
        body = body_text if status == "success" and body_text else None   # failed / rejected / pending: no text
        # With no stored text only the title can show that the article is about the company.
        about = is_about_company(title, body, company_name, ticker)
        lead = ""
        if about and body and text_slots > 0:
            lead = _snippet(body, max_chars=text_chars)
            text_slots -= 1
        articles.append(NewsArticleItem(
            title=title,
            source="yahoo_finance",
            published_at=str(published_at) if published_at else None,
            snippet=lead or _snippet(title),
            has_article_text=bool(lead),
            about_company=about,
        ))

    filings = [
        SecFilingItem(form_type=form_type or "", filing_date=filing_date, section=section or "",
                      snippet=_snippet(text))
        for form_type, filing_date, section, text in filing_rows
    ]

    output = NewsToolOutput(
        ticker=ticker,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        recent_articles=articles,
        recent_sec_filings=filings,
    )
    return output