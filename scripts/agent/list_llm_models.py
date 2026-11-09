"""
List the model names your LLM endpoint accepts for your API key, and say whether the model
currently configured (LLM_MODEL in .env) is among them.

    python scripts/agent/list_llm_models.py

Uses LLM_BASE_URL, MANAGED_LLM_API_KEY and LLM_MODEL from .env (src/equity_pipeline/config.py).
The key is sent only to your own LLM_BASE_URL and is never printed. Read-only: one GET /models.
Use the output to set LLM_MODEL in .env when the screening agent fails with
"Invalid model name ... Call /v1/models to view available models for your key".
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from equity_pipeline.config import llm_settings  # noqa: E402


def main() -> None:
    s = llm_settings()
    if not s["api_key"] or not s["base_url"]:
        sys.exit("MANAGED_LLM_API_KEY and LLM_BASE_URL must be set in .env first.")
    url = f"{s['base_url'].rstrip('/')}/models"
    print(f"endpoint: {s['base_url']}\nconfigured model: {s['model']}\n")
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {s['api_key']}"}, timeout=30)
    except requests.RequestException as exc:
        sys.exit(f"Could not reach {url}: {exc}")
    if r.status_code != 200:
        sys.exit(f"{url} answered {r.status_code}: {r.text[:300]}")

    ids = sorted(m.get("id", "") for m in r.json().get("data", []))
    print(f"{len(ids)} models available for this key:")
    for model_id in ids:
        print(f"  {'->' if model_id == s['model'] else '  '} {model_id}")
    if s["model"] in ids:
        print(f"\nThe configured model '{s['model']}' IS available: the 400 error has another cause.")
    else:
        print(f"\nThe configured model '{s['model']}' is NOT available. Put one of the names above in .env:")
        print("    LLM_MODEL=<name from the list>")


if __name__ == "__main__":
    main()
