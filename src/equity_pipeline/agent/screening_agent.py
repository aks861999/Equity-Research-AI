"""
The investment-screening agent, built with PydanticAI.

Constraints from task.txt honoured explicitly:
  - Accepts a company name or ticker as input                  -> run_screening(ticker)
  - Uses exactly two tools (news tool + fundamentals tool)      -> @screening_agent.tool x2, nothing else
  - Produces a human-readable screening summary for a PM        -> ScreeningSummary (schemas.py)
  - Does not make the final investment decision                 -> system prompt + growth_relevance_verdict
    is constrained to a small enum-like set of non-decision labels, never buy/sell/hold.
  - Analysis limited to companies in config/companies.yaml       -> get_company() raises if ticker unknown,
    which happens before any tool/LLM call.

Install: `pip install pydantic-ai` (in addition to the repo's existing requirements.txt).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import get_company, llm_settings, missing_llm_settings
from .data_access import get_db, load_fundamentals_bundle, load_news_bundle
from .monitoring import RunMonitor
from .pipeline_runner import run_live_pipeline
from .reproducibility import (
    DEFAULT_SEED,
    build_run_manifest,
    deterministic_model_settings,
    new_run_id,
    save_run_manifest,
)
from .schemas import FundamentalsToolOutput, NewsToolOutput, ScreeningSummary

logger = logging.getLogger("screening_agent")


@dataclass
class AgentDeps:
    ticker: str
    db: object
    monitor: RunMonitor
    # What each tool returned, kept so the run manifest can hash the real inputs.
    tool_payloads: dict = field(default_factory=dict)


def red_risk_count(fundamentals_payload: dict) -> int:
    """How many risk metrics are graded Red in what fundamentals_tool returned: the real value of
    `risk_flag_count`, which the model is only asked to repeat."""
    return sum(1 for m in fundamentals_payload.get("risk_metrics", [])
               if str(m.get("fundamental_grade", "")).lower() == "red")


def _build_model(settings: dict):
    """PydanticAI's OpenAI-compatible model, pointed at the endpoint set in .env
    (LLM_BASE_URL, LLM_MODEL, MANAGED_LLM_API_KEY; see config.llm_settings)."""
    provider = OpenAIProvider(base_url=settings["base_url"], api_key=settings["api_key"])
    return OpenAIChatModel(settings["model"], provider=provider)


SYSTEM_PROMPT = """\
You are an equity-research assistant supporting a Portfolio Manager's early-stage \
screening process. You are NOT making an investment decision; you are producing a \
concise, human-readable screening summary that helps a human PM decide whether to \
look further.

You have exactly two tools available:
  - fundamentals_tool: returns 25 growth-strategy fundamental metrics (each with its \
own weighted_sentiment/dispersion/volume from news matched specifically to that \
metric, where available) and 11 risk fundamental metrics (no per-metric sentiment \
exists for these), plus company-level general signals: the Yahoo Finance items that \
were scored (title, date, sentiment) and a short description of SEC filing activity.
  - news_tool: returns the newest Yahoo Finance headlines (for the newest few, the lead of \
the article text, marked has_article_text) and the recent SEC filing excerpts, as stored in \
the database by the refresh that runs just before you (so they are as current as that \
refresh; with the refresh off they are the last stored). The filing excerpts are ONLY here: \
the SEC signal in fundamentals_tool describes filing activity (count, form types, dates) \
without excerpts.

You MUST call both tools exactly once each before answering. Base every claim only on \
the data the tools returned - never invent news, numbers, or filings. You may say what a \
ratio MEASURES (for example that a current ratio compares short-term assets with short-term \
liabilities). Do NOT explain WHY a company's figure is what it is, do NOT describe a trend or \
direction over time (the ratios are one annual snapshot; only the growth rates compare years), \
and do NOT add company facts (share buybacks, acquisitions, accounting choices) unless a tool \
returned them. If something cannot be explained from the returned data, say so in a few words \
instead of guessing.

Answer using the ScreeningSummary schema, addressing:
1. Growth relevance - reason over ALL 25 growth fundamental metrics together with \
their matched weighted_sentiment/dispersion/volume (where present). A metric with high \
sentiment_dispersion or low sentiment_volume should be treated as a weaker signal, not \
weighted the same as a metric with strong agreement and volume. Where weighted_sentiment \
is None, say so explicitly rather than treating it as neutral.
2. Positives/negatives from recent news - ground these in the specific articles/filings \
news_tool returned.
3. Financial highlights - a plain-language high-level read of growth, profitability, \
capital efficiency, and leverage/liquidity, grounded only in the fundamental_value/grade \
figures actually supplied. If a figure is missing, say so instead of guessing.
4. Risk concerns - reason over the risk fundamental metrics AND the general \
(company-level, not metric-specific) Yahoo Finance / SEC EDGAR sentiment signals from \
fundamentals_tool. The Yahoo general_sentiment entry has an `evidence` list of scored \
items (title, date, sentiment): cite specific items from it rather than only restating \
the aggregate label. For what the company actually disclosed, cite the filing excerpts \
in news_tool.recent_sec_filings (the SEC general_sentiment entry only tells you how \
many filings there were and when). Be explicit that these signals are \
general/company-wide, not specific to any one risk metric, since no per-metric risk \
sentiment was computed. An article snippet with has_article_text false is only a \
headline: do not describe its content beyond the title. A news_tool article with \
about_company false is a market-wide or unrelated piece that at most mentions the company \
in passing: do not use it as evidence about the company (the scored Yahoo evidence \
already leaves such items out).

Time periods - read this before writing Q1 and Q3. The fundamental ratios come from the \
company's latest ANNUAL statements; fundamentals_tool.period gives the fiscal_year_end, the \
period_type and the currency. Filings and news often quote other periods (a quarter, a later \
quarter, a different currency or a growth rate between quarters), so their figures will NOT \
match the ratios. When you mention a figure from news or a filing, name its period (for \
example "Q3 2026") and never put it side by side with a ratio as if both covered the same \
period. If a quarterly figure and a ratio disagree, say they cover different periods; do NOT \
explain the difference with guesses such as "trailing", "average" or "adjusted basis". \
Amounts (free cash flow) are in period.currency. If period is missing, say the period of \
the ratios is not stated.

Length: each of growth_relevance_reasoning, financial_highlights and risk_concerns at most \
120 words (about 800 characters); news_narrative_summary at most 70 words; each bullet in \
key_positives / key_negatives one sentence of at most 30 words. Prefer the most decision-\
relevant facts over listing every metric.

Never output a buy/sell/hold recommendation or price target. growth_relevance_verdict \
must be one of: "Potentially relevant", "Not clearly relevant", "Insufficient data".
"""


def build_agent() -> Agent:
    settings = llm_settings()
    missing = missing_llm_settings(settings)
    if missing:
        raise RuntimeError(f"The LLM is not configured: set {', '.join(missing)} in .env "
                           "(or the host's secrets). See .env.example.")
    model = _build_model(settings)

    agent = Agent(
        model,
        deps_type=AgentDeps,
        output_type=ScreeningSummary,
        system_prompt=SYSTEM_PROMPT,
        model_settings=deterministic_model_settings(),
    )

    @agent.tool
    async def fundamentals_tool(ctx: RunContext[AgentDeps]) -> FundamentalsToolOutput:
        """Growth (25) + risk (11) fundamental metrics from the provided SEC-derived
        database tables, each metric's news-derived weighted_sentiment/dispersion/
        volume where available, and general company-level sentiment signals from
        Yahoo Finance news and SEC EDGAR filings (risk metrics have no per-metric
        sentiment, so this general signal is supplied for the risk question).
        `period` states which ANNUAL statements the ratios come from (fiscal year-end,
        currency)."""
        ctx.deps.monitor.record_tool_call("fundamentals_tool")
        bundle = load_fundamentals_bundle(ctx.deps.db, ctx.deps.ticker)
        # The fetch timestamp is left out: it differs every run and would change the hash alone.
        ctx.deps.tool_payloads["fundamentals"] = bundle.model_dump(exclude={"data_as_of"})
        return bundle

    @agent.tool
    async def news_tool(ctx: RunContext[AgentDeps]) -> NewsToolOutput:
        """The newest Yahoo Finance headlines (the newest few with the lead of the article
        text) and recent SEC EDGAR filing excerpts for the ticker, read from the database
        where the refresh step just stored them. This is the only tool that carries the
        filing excerpts."""
        ctx.deps.monitor.record_tool_call("news_tool")
        bundle = load_news_bundle(ctx.deps.ticker, db=ctx.deps.db)
        ctx.deps.tool_payloads["news"] = bundle.model_dump(exclude={"fetched_at"})
        return bundle

    return agent


def run_screening(
    ticker: str,
    seed: int = DEFAULT_SEED,
    refresh_data: bool = True,
    include_news: bool = False,
    progress_callback=None,
) -> dict:
    ticker = ticker.strip().upper()
    company = get_company(ticker)
    logger.info("Starting screening run for %s (%s)", ticker, company["primary_search_name"])

    run_id = new_run_id(ticker)
    monitor = RunMonitor(run_id=run_id, ticker=ticker, progress_callback=progress_callback)
    db = get_db()

    pipeline_counts = None
    if refresh_data:
        pipeline_counts = run_live_pipeline(
            db, ticker, include_news=include_news, progress_callback=progress_callback,
        )

    if progress_callback:
        progress_callback("🤖 Calling screening agent (fundamentals_tool + news_tool + LLM)...")

    agent = build_agent()
    deps = AgentDeps(ticker=ticker, db=db, monitor=monitor)

    run_result = agent.run_sync(
        f"Produce an initial growth-strategy screening summary for {ticker} "
        f"({company['primary_search_name']}).",
        deps=deps,
        model_settings=deterministic_model_settings(seed),
    )


    if progress_callback:
        progress_callback("📝 Finalizing screening summary...")


    output: ScreeningSummary = run_result.output
    usage = run_result.usage
    monitoring_summary = monitor.summary(usage)

    # risk_flag_count is a number the data already knows, so the model's count is not trusted: it is
    # replaced by the real count of Red risk grades, and a difference is recorded and logged.
    risk_flag_correction = None
    fundamentals_payload = deps.tool_payloads.get("fundamentals")
    if fundamentals_payload:
        actual_red = red_risk_count(fundamentals_payload)
        if output.risk_flag_count != actual_red:
            risk_flag_correction = {"model_said": output.risk_flag_count, "actual": actual_red}
            logger.warning("risk_flag_count: the model wrote %d, the data has %d Red risk metrics; corrected",
                           output.risk_flag_count, actual_red)
            output.risk_flag_count = actual_red

    manifest = build_run_manifest(
        run_id=run_id,
        ticker=ticker,
        model_name=llm_settings()["model"],
        seed=seed,
        # Only hashed, never stored, so manifests stay small.
        fundamentals_payload=deps.tool_payloads.get("fundamentals", {}),
        news_payload=deps.tool_payloads.get("news", {}),
        monitoring_summary=monitoring_summary,
        final_output=output.model_dump(),
        risk_flag_correction=risk_flag_correction,   # None when the model counted right
    )
    manifest["pipeline_counts"] = pipeline_counts
    manifest_path = save_run_manifest(manifest)

    logger.info(
        "Screening run %s complete: %d tool calls, %s LLM calls",
        run_id, monitoring_summary["tool_calls_total"], monitoring_summary["llm_calls_total"],
    )

    return {
        "result": output,
        "monitoring": monitoring_summary,
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "pipeline_counts": pipeline_counts,
        "risk_flag_correction": risk_flag_correction,
        # What the news tool actually returned, for the groundedness check (figures quoted from
        # filings and article leads are grounded too).
        "news_payload": deps.tool_payloads.get("news", {}),
    }