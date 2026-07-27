"""
Streamlit dashboard for the Portfolio Manager.

Run with:
    streamlit run streamlit_app.py

Requires: pip install streamlit pydantic-ai (plus the repo's requirements.txt),
Postgres reachable per .env, and MANAGED_LLM_API_KEY set.
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from equity_pipeline.agent.data_access import get_db, load_fundamentals_bundle  # noqa: E402
from equity_pipeline.agent.evaluation import evaluate_screening  # noqa: E402
from equity_pipeline.agent.reproducibility import DEFAULT_SEED  # noqa: E402
from equity_pipeline.agent.screening_agent import run_screening  # noqa: E402
from equity_pipeline.config import load_companies  # noqa: E402

st.set_page_config(page_title="Growth Screening Agent", layout="wide")
st.title("📈 Investment Screening Agent")
st.caption("Early-stage growth-strategy screening. Not an investment recommendation.")

companies = load_companies()
ticker_options = [c["ticker"] for c in companies]

col1, col2 = st.columns([3, 1])
with col1:
    ticker = st.selectbox("Ticker", ticker_options)
with col2:
    seed = st.number_input("Seed", value=DEFAULT_SEED, step=1)

refresh_data = st.checkbox(
    "Run full live pipeline first (SEC EDGAR + Yahoo Finance + Google News ingest, FinBERT sentiment, "
    "aggregation, growth + risk fundamentals)",
    value=True,
    help="Uncheck for a fast run using whatever is already in Postgres. Checked, a single click can take "
         "anywhere from ~15 seconds to a couple of minutes depending on how much is new.",
)
include_google_news = True
if refresh_data:
    include_google_news = st.checkbox(
        "Include Google News per-metric ingest (needed for growth-metric sentiment; slowest step)",
        value=False,
    )

if st.button("Run screening", type="primary"):
    with st.spinner(f"Running screening agent for {ticker}..."):
        run = run_screening(
            ticker, seed=int(seed), refresh_data=refresh_data, include_google_news=include_google_news,
        )
        summary = run["result"]
        db = get_db()
        fundamentals = load_fundamentals_bundle(db, ticker)
        quality = evaluate_screening(summary, fundamentals)

    st.success(f"Screening complete for {summary.ticker}")

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
    st.write(summary.risk_concerns)

    st.info(summary.disclaimer)

    with st.expander("🔍 Monitoring (tool calls / LLM calls)"):
        st.json(run["monitoring"])

    if run["pipeline_counts"] is not None:
        with st.expander("🔄 Live pipeline run (SEC EDGAR / Yahoo / Google News / FinBERT / fundamentals)"):
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
