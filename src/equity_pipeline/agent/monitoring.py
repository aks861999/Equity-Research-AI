"""
Additional Requirement 1: Monitoring.

Two things need counting per screening run:
  1. Tool calls  - how many times fundamentals_tool / news_tool actually ran.
     PydanticAI does not expose this by itself (it only tracks LLM usage),
     so we increment a counter ourselves from inside each @agent.tool
     function via RunContext.deps.
  2. LLM calls   - how many requests were sent to the model. PydanticAI's
     `result.usage()` already tracks this (agent.run() can involve more than
     one LLM round-trip: an initial call, then follow-up calls after each
     tool result, then the final structured-output call), so we read it
     straight off the RunResult rather than re-counting by hand.

Everything here is plain, dependency-free bookkeeping so it can be unit
tested without a live model or database.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RunMonitor:
    run_id: str
    ticker: str
    tool_call_log: list[dict] = field(default_factory=list)
    _start_time: float = field(default_factory=time.monotonic)

    def record_tool_call(self, tool_name: str, **extra) -> None:
        self.tool_call_log.append(
            {
                "tool_name": tool_name,
                "elapsed_seconds": round(time.monotonic() - self._start_time, 3),
                **extra,
            }
        )

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_call_log)

    def tool_call_count_by_name(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.tool_call_log:
            counts[entry["tool_name"]] = counts.get(entry["tool_name"], 0) + 1
        return counts

    def summary(self, llm_usage) -> dict:
        """`llm_usage` is whatever object `result.usage()` returns from
        pydantic_ai (has .requests, .request_tokens, .response_tokens,
        .total_tokens in recent versions)."""
        return {
            "run_id": self.run_id,
            "ticker": self.ticker,
            "tool_calls_total": self.tool_call_count,
            "tool_calls_by_name": self.tool_call_count_by_name(),
            "tool_call_log": self.tool_call_log,
            "llm_calls_total": getattr(llm_usage, "requests", None),
            "llm_request_tokens": getattr(llm_usage, "input_tokens", None),
            "llm_response_tokens": getattr(llm_usage, "output_tokens", None),
            "llm_total_tokens": getattr(llm_usage, "total_tokens", None),
            "wall_clock_seconds": round(time.monotonic() - self._start_time, 3),
        }