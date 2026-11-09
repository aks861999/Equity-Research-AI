"""
Streamlit dashboard for the Portfolio Manager, and the headless WORKER of the web front end.

Run from the project root:
    streamlit run apps/dashboard/streamlit_app.py

Two ways in:

  1. A person opens it and presses "Run screening" (the interactive page below).
  2. The web front end (apps/web) opens it in a hidden frame with query parameters and polls the
     `screening_runs` table for the result:

         ?autorun=1&ticker=MELI&seed=42&refresh=1&nonce=<number made up per click>
         ?ping=1                                       answers at once; used to keep the app awake

     In the worker case nothing is drawn: every pipeline step is written to the `screening_runs`
     table (apps/web reads it), and so is the finished brief, its quality checks and its manifest.

In APP_MODE=demo (the online app) a run is limited by src/equity_pipeline/agent/run_store.py: one run per
ticker at a time and a finished result is reused for a few minutes, so the public app cannot be used to
run the language model in a loop.

Requires: the packages of requirements.txt, Postgres reachable per .env (or the host's secrets), and the
three LLM settings.
"""
import logging
import re
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from equity_pipeline.agent import run_store  # noqa: E402
from equity_pipeline.agent.data_access import (  # noqa: E402
    fetch_data_freshness,
    fetch_fundamental_metrics_df,
    fetch_metric_scores_df,
    fetch_reference_sources,
    get_db,
    load_fundamentals_bundle,
    sec_filings_page_url,
)
from equity_pipeline.agent.evaluation import evaluate_screening  # noqa: E402
from equity_pipeline.agent.reproducibility import DEFAULT_SEED, new_run_id  # noqa: E402
from equity_pipeline.agent.screening_agent import run_screening  # noqa: E402
from equity_pipeline.config import app_mode, is_demo, load_companies  # noqa: E402

logger = logging.getLogger("dashboard")

DEMO = is_demo()  # APP_MODE=demo: the online app (live SEC EDGAR + Yahoo only)
MODE = app_mode()

st.set_page_config(page_title="Growth Screening Agent", layout="wide")

companies = load_companies()
ticker_options = [c["ticker"] for c in companies]  # only the configured companies: their news and scores are prepared


# ---------------------------------------------------------------------------
# Running a screening and saving it (shared by the worker and the interactive page)
# ---------------------------------------------------------------------------
def _safely(label: str, fn, *args, **kwargs) -> None:
    """Saving progress must never break a screening: a failure is logged and the run goes on."""
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        logger.warning("could not %s: %s", label, exc)


def execute_and_persist_run(ticker: str, seed: int, do_refresh: bool, do_news: bool, nonce: str | None = None,
                            show_progress=None):
    """Runs the screening, writes every step and the result to `screening_runs`, returns (run, quality)."""
    db = get_db()
    run_id = new_run_id(ticker)
    _safely("save the start of the run", run_store.update_progress, db, ticker, run_id,
            f"[INIT] Starting {MODE.upper()} screening pipeline for {ticker}...", mode=MODE, seed=seed, nonce=nonce)

    def _on_progress(message: str) -> None:
        _safely("save progress", run_store.update_progress, db, ticker, run_id, message,
                mode=MODE, seed=seed, nonce=nonce)
        if show_progress:
            show_progress(message)

    try:
        run = run_screening(ticker, seed=int(seed), refresh_data=do_refresh, include_news=do_news,
                            progress_callback=_on_progress)
        summary = run["result"]
        fundamentals = load_fundamentals_bundle(db, ticker)
        quality = evaluate_screening(summary, fundamentals, news=run.get("news_payload"))
        _safely("save the finished run", run_store.save_completed, db, ticker, run["manifest"]["run_id"], MODE,
                int(seed), summary.model_dump(), run_store.quality_payload(quality), run["monitoring"],
                run["pipeline_counts"], run["manifest"], nonce=nonce)
        return run, quality
    except Exception as exc:
        _safely("save the error", run_store.record_error, db, ticker, run_id, str(exc), nonce=nonce)
        raise


# ---------------------------------------------------------------------------
# Headless entry points (the web front end, the keep-awake ping)
# ---------------------------------------------------------------------------
qp = st.query_params

if qp.get("ping") == "1":
    st.write("OK: WORKER AWAKE")
    st.stop()

if qp.get("autorun") == "1":
    nonce = qp.get("nonce") or ""
    ticker_q = (qp.get("ticker") or "").upper()
    try:
        seed_q = int(qp.get("seed") or DEFAULT_SEED)
    except ValueError:
        seed_q = DEFAULT_SEED

    if ticker_q not in ticker_options:
        st.error("WORKER REFUSED: ticker is not configured.")
        st.stop()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", nonce):
        st.error("WORKER REFUSED: a nonce (letters, digits, - and _, at most 64) is required.")
        st.stop()
    if st.session_state.get("worker_nonce") == nonce:       # Streamlit reran the script inside the same session
        st.write(f"WORKER COMPLETE: {ticker_q} (nonce={nonce})")
        st.stop()
    st.session_state["worker_nonce"] = nonce

    allowed, reason = run_store.may_start(get_db(), ticker_q, nonce)
    if not allowed:
        if reason == "recent_result":
            run_store.acknowledge(get_db(), ticker_q, nonce, "RECENT RESULT REUSED")
        st.write(f"WORKER SKIPPED: {reason}")
        st.stop()

    execute_and_persist_run(ticker_q, seed_q, qp.get("refresh", "1") != "0", False, nonce=nonce)
    st.write(f"WORKER COMPLETE: {ticker_q} (nonce={nonce})")
    st.stop()


# ---------------------------------------------------------------------------
# The interactive page
# ---------------------------------------------------------------------------
st.title("📈 Investment Screening Agent")
st.caption("Early-stage growth-strategy screening. Not an investment recommendation.")

col1, col2 = st.columns([3, 1])
with col1:
    ticker = st.selectbox("Ticker", ticker_options)
with col2:
    seed = st.number_input("Seed", value=DEFAULT_SEED, step=1)

if DEMO:
    _fresh = fetch_data_freshness(get_db(), ticker)
    st.info(
        f"**Live in this demo:** SEC EDGAR filings and Yahoo Finance headlines, read when you press Run.  \n"
        f"**Prepared offline, last updated {_fresh['news_last_updated'] or 'n/a'}:** the search news and its "
        f"sentiment scores (per-metric sentiment scores calculated {_fresh['scores_last_updated'] or 'n/a'}).  \n"
        f"**Financial ratios:** annual statements for the fiscal year ending {_fresh['fiscal_year_end'] or 'n/a'}, "
        f"calculated {_fresh['ratios_last_updated'] or 'n/a'}."
    )

include_news = False
if DEMO:
    refresh_data = st.checkbox(
        "Refresh live data first (new SEC EDGAR filings and Yahoo Finance headlines)",
        value=True,
        help="Uncheck to use only what is already stored. Article text is read with plain downloads only; "
             "pages that need a browser are skipped.",
    )
else:
    refresh_data = st.checkbox(
        "Run full live pipeline first (SEC EDGAR + Yahoo Finance + news search ingest, FinBERT sentiment, "
        "aggregation, growth + risk fundamentals)",
        value=True,
        help="Uncheck for a fast run using whatever is already in Postgres. Checked, a single click can take "
             "anywhere from ~15 seconds to a couple of minutes depending on how much is new.",
    )
    if refresh_data:
        include_news = st.checkbox(
            "Include per-metric news search (local SearXNG; needed for growth-metric sentiment; slowest step)",
            value=False,
        )
with st.expander(f"🗄️ Raw DB tables for {ticker}", expanded=False):
    _preview_db = get_db()
    st.markdown("**fundamental_metrics**")
    st.dataframe(fetch_fundamental_metrics_df(_preview_db, ticker), width="stretch")
    st.markdown("**metric_scores**")
    st.dataframe(fetch_metric_scores_df(_preview_db, ticker), width="stretch")


if st.button("Run screening", type="primary"):
    blocked = None
    if DEMO:  # the public app: one run per ticker at a time, a fresh result is reused
        allowed, reason = run_store.may_start(get_db(), ticker, None)
        if not allowed:
            blocked = ("A screening for this ticker is already running." if reason == "running" else
                       "A screening for this ticker finished a few minutes ago. Its result is in the web "
                       "terminal; please wait a little before running it again.")
    if blocked:
        st.warning(blocked)
        st.stop()

    with st.status(f"Running screening agent for {ticker}...", expanded=True) as status:
        run, quality = execute_and_persist_run(ticker, int(seed), refresh_data, include_news,
                                               show_progress=status.write)
        summary = run["result"]
        status.update(label=f"Screening complete for {ticker}", state="complete")

    # What the live fetch found: new items, nothing new (everything fetched is already stored), or a
    # source that could not be reached.
    for notice in (run["pipeline_counts"] or {}).get("notices", []):
        getattr(st, notice["level"], st.info)(notice["text"])

    st.subheader("1. Growth-strategy relevance")
    verdict_color = {
        "Potentially relevant": "green",
        "Not clearly relevant": "orange",
        "Insufficient data": "gray",
    }.get(summary.growth_relevance_verdict, "blue")
    st.markdown(f"**Verdict:** :{verdict_color}[{summary.growth_relevance_verdict}]")
    st.write(summary.growth_relevance_reasoning)

    st.subheader("2. Key positives / negatives from recent news")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Positives**")
        for p in summary.key_positives:
            st.markdown(f"- ✅ {p}")
    with c2:
        st.markdown("**Negatives**")
        for n in summary.key_negatives:
            st.markdown(f"- ⚠️ {n}")
    st.caption(summary.news_narrative_summary)

    st.subheader("3. Financial highlights")
    st.write(summary.financial_highlights)

    st.subheader("4. Risk concerns")
    st.metric("Risk metrics graded Red", summary.risk_flag_count)
    if run.get("risk_flag_correction"):
        st.caption(f"The model wrote {run['risk_flag_correction']['model_said']}; the count shown is the real "
                   f"number of Red risk grades in the data.")
    st.write(summary.risk_concerns)

    st.info(summary.disclaimer)

    with st.expander("🔍 Monitoring (tool calls / LLM calls)"):
        st.json(run["monitoring"])

    if run["pipeline_counts"] is not None:
        with st.expander("🔄 Live pipeline run" + (" (SEC EDGAR / Yahoo Finance)" if DEMO else
                                                    " (SEC EDGAR / Yahoo / search news / FinBERT / ratios)")):
            st.json(run["pipeline_counts"])

    with st.expander("✅ Quality evaluation"):
        st.metric("Overall quality score", f"{quality.score:.0%}")
        for name, passed in quality.checks.items():
            icon = "✅" if passed else "❌"
            detail = quality.details.get(name, "")
            st.markdown(f"{icon} **{name}** {(' — ' + detail) if detail else ''}")

    with st.expander("🧾 Reproducibility manifest"):
        st.json(run["manifest"])
        st.caption(f"Saved to: {run['manifest_path']}")
else:
    st.write("Select a ticker and click **Run screening** to generate a PM-ready summary.")


# ---------------------------------------------------------------------------
# Reference sources: the links the data comes from
# ---------------------------------------------------------------------------
st.divider()
st.subheader(f"📎 Reference sources for {ticker}")
st.markdown(
    f"**Financial statements** (all growth and risk ratios): SEC filings of {ticker}, "
    f"fetched through OpenBB. [Open the filing list on SEC EDGAR]({sec_filings_page_url(ticker)})"
)
_sources = fetch_reference_sources(get_db(), ticker)
if _sources.empty:
    st.info("No articles or filings stored for this ticker yet. Run the data preparation first "
            "(`scripts/data-prep/fetch_news.py`) or tick the live pipeline above.")
else:
    _scored_only = st.checkbox("Only articles that contributed to a sentiment score", value=False)
    _shown = _sources[_sources["Sentiment"] != ""] if _scored_only else _sources
    _n_news = int(_sources["Type"].str.startswith("News").sum())
    _n_scored = int((_sources["Sentiment"] != "").sum())
    st.caption(f"{len(_sources)} sources: {_n_news} news articles ({_n_scored} scored by FinBERT), "
               f"{len(_sources) - _n_news} SEC filing sections. Metrics = the metric queries that found the article "
               f"(m = growth). Date from: source = search engine or feed, page = read from the article page.")
    st.dataframe(
        _shown,
        width="stretch",
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("Link"),
            "Title": st.column_config.TextColumn("Title", width="large"),
        },
    )
