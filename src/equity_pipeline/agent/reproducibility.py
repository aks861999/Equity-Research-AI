"""
Additional Requirement 2: Reproducibility.

An LLM-in-the-loop pipeline has three independent sources of
non-determinism, and each is handled separately here:

1. Model sampling randomness
   -> model_settings sets temperature=0 and top_p=0 (greedy decoding) and,
      if the backing model supports it, a fixed `seed`. This does not make
      outputs bit-for-bit identical on every provider (some hosted models
      are not perfectly deterministic even at temperature=0), but it removes
      the single biggest source of run-to-run drift and is the standard,
      practical definition of "reproducible" for LLM pipelines.

2. Input data drift
   -> the CSV fundamentals and DB-derived metric_scores are a snapshot in
      time. `build_run_manifest()` hashes the exact data pulled into the
      prompt (fundamentals_tool + news_tool output) so that any two runs can
      be diffed: if the hash matches and the model/temperature/prompt
      version match, any output difference is attributable to model
      non-determinism rather than "the data changed under us".

3. Prompt/schema drift
   -> PROMPT_VERSION is bumped by hand whenever the system prompt or the
      Pydantic output schema changes, and is stored in every manifest, so a
      changed prompt can never masquerade as a reproducibility failure.

Every run writes a JSON manifest to disk (run_manifests/<run_id>.json) so a
PM asking "why did this change since last week" always has an artifact to
point to, independent of chat/dashboard history.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROMPT_VERSION = "screening-agent-v1"
DEFAULT_SEED = 42
MANIFEST_DIR = Path(__file__).resolve().parents[3] / "run_manifests"


def new_run_id(ticker: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ticker}_{ts}_{uuid.uuid4().hex[:8]}"


# after
def deterministic_model_settings(seed: int = DEFAULT_SEED) -> dict:
    return {
        "temperature": 0.0,
        "top_p": 1e-5,
        "seed": seed,
    }


def _stable_hash(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def build_run_manifest(
    run_id: str,
    ticker: str,
    model_name: str,
    seed: int,
    fundamentals_payload: dict,
    news_payload: dict,
    monitoring_summary: dict,
    final_output: dict,
) -> dict:
    manifest = {
        "run_id": run_id,
        "ticker": ticker,
        "prompt_version": PROMPT_VERSION,
        "model_name": model_name,
        "seed": seed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_data_hash": _stable_hash({"fundamentals": fundamentals_payload, "news": news_payload}),
        "monitoring": monitoring_summary,
        "final_output": final_output,
    }
    return manifest


def save_run_manifest(manifest: dict) -> Path:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFEST_DIR / f"{manifest['run_id']}.json"
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    return path