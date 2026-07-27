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
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import get_company, llm_settings
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


def _build_model(settings: dict):
    """PydanticAI's OpenAI-compatible model, pointed at the same
    fhgenie/OpenAI-compatible endpoint the rest of the codebase already uses
    (see config.llm_settings / dashboard_run_news_summarization.py)."""
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
exists for these), plus company-level general sentiment signals from Yahoo Finance \
news and SEC EDGAR filings.
  - news_tool: returns current news headlines and recent SEC filing excerpts fetched \
live from the internet.

You MUST call both tools exactly once each before answering. Base every claim only on \
the data returned by these tools plus commonly known facts about how financial ratios \
are interpreted - never invent news, numbers, or filings that were not returned.

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
fundamentals_tool. Each general_sentiment entry includes an `evidence` list of short, \
individual article/filing summaries (title, date, sentiment or content snippet) in \
addition to the aggregate `summary` - cite specific items from `evidence` rather than \
only restating the aggregate label, and be explicit that these signals are \
general/company-wide, not specific to any one risk metric, since no per-metric risk \
sentiment was computed.

Never output a buy/sell/hold recommendation or price target. growth_relevance_verdict \
must be one of: "Potentially relevant", "Not clearly relevant", "Insufficient data".
"""


def build_agent() -> Agent:
    settings = llm_settings()
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
        """Growth (25) + risk (11) fundamental metrics from the provided CSV-derived
        database tables, each metric's news-derived weighted_sentiment/dispersion/
        volume where available, and general company-level sentiment signals from
        Yahoo Finance news and SEC EDGAR filings (risk metrics have no per-metric
        sentiment, so this general signal is supplied for the risk question)."""
        ctx.deps.monitor.record_tool_call("fundamentals_tool")
        return load_fundamentals_bundle(ctx.deps.db, ctx.deps.ticker)

    @agent.tool
    async def news_tool(ctx: RunContext[AgentDeps]) -> NewsToolOutput:
        """Current news flow from the internet: live Yahoo Finance headlines and
        recent SEC EDGAR filing excerpts for the ticker."""
        ctx.deps.monitor.record_tool_call("news_tool")
        return load_news_bundle(ctx.deps.ticker)

    return agent


def run_screening(
    ticker: str,
    seed: int = DEFAULT_SEED,
    refresh_data: bool = True,
    include_google_news: bool = False,
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
            db, ticker, include_google_news=include_google_news, progress_callback=progress_callback,
        )

    if progress_callback:
        progress_callback("🤖 Calling screening agent (fundamentals_tool + news_tool + LLM)...")

    agent = build_agent()
    deps = AgentDeps(ticker=ticker, db=db, monitor=monitor)
    run_result = agent.run_sync(..., deps=deps, model_settings=deterministic_model_settings(seed))

    if progress_callback:
        progress_callback("📝 Finalizing screening summary...")


    output: ScreeningSummary = run_result.output
    usage = run_result.usage
    monitoring_summary = monitor.summary(usage)

    manifest = build_run_manifest(
        run_id=run_id,
        ticker=ticker,
        model_name=llm_settings()["model"],
        seed=seed,
        fundamentals_payload={},  # populated below if you want full input replay; omitted here to keep manifests small
        news_payload={},
        monitoring_summary=monitoring_summary,
        final_output=output.model_dump(),
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
    }