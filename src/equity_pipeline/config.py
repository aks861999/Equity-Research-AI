"""
Central config loading: .env (CouchDB creds, LLM creds) + config/companies.yaml
+ config/metrics.yaml. Every other module takes plain dicts/strings as
arguments rather than reading files itself, so this is the only place that
touches disk for configuration.
"""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Repo root = three levels up from this file (src/equity_pipeline/config.py -> repo root)
ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT_DIR / "config"

load_dotenv(ROOT_DIR / ".env")


def load_companies() -> list[dict]:
    with open(CONFIG_DIR / "companies.yaml") as f:
        data = yaml.safe_load(f)
    return data["companies"]


def get_company(ticker: str) -> dict:
    for company in load_companies():
        if company["ticker"].upper() == ticker.upper():
            return company
    raise ValueError(f"No entry for ticker '{ticker}' in config/companies.yaml")


def load_metrics() -> list[dict]:
    with open(CONFIG_DIR / "metrics.yaml") as f:
        data = yaml.safe_load(f)
    return data["metrics"]


def db_settings() -> dict:
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
        "db_name": os.getenv("POSTGRES_DB", "equity_research"),
        "user": os.getenv("POSTGRES_USER"),
        "password": os.getenv("POSTGRES_PASSWORD"),
        # empty for the local container; "require" for a hosted database such as Supabase
        "sslmode": os.getenv("POSTGRES_SSLMODE", "").strip() or None,
    }


def target_database_url() -> str:
    """Connection URL of the hosted database the prepared data is published to (for example the
    Supabase "session pooler" string). Used only by scripts/online/*; the app itself reads POSTGRES_*."""
    return os.getenv("TARGET_DATABASE_URL", "").strip()


def llm_settings() -> dict:
    """The OpenAI-compatible endpoint of the screening agent. No defaults: all three come from the
    environment (.env locally, the host's secret store online); `missing_llm_settings` names what is unset."""
    return {
        "base_url": os.getenv("LLM_BASE_URL", "").strip(),
        "api_key": os.getenv("MANAGED_LLM_API_KEY", "").strip(),
        "model": os.getenv("LLM_MODEL", "").strip(),
    }


def missing_llm_settings(settings: dict | None = None) -> list[str]:
    """Names of the LLM environment variables that are not set."""
    s = settings or llm_settings()
    names = {"base_url": "LLM_BASE_URL", "api_key": "MANAGED_LLM_API_KEY", "model": "LLM_MODEL"}
    return [env for key, env in names.items() if not s.get(key)]


def sec_user_agent() -> str:
    """SEC EDGAR rejects requests without a real contact: '<name> <email>'."""
    return os.getenv("SEC_USER_AGENT", "")


def app_mode() -> str:
    """'full' (default, your PC: every step incl. SearXNG, FinBERT and the browser fallback) or
    'demo' (the online dashboard: live SEC EDGAR + Yahoo Finance only, text read with curl_cffi only,
    no SearXNG, no FinBERT, no browser; everything else is read from the shared database)."""
    mode = os.getenv("APP_MODE", "full").strip().lower()
    return mode if mode in ("full", "demo") else "full"


def is_demo() -> bool:
    return app_mode() == "demo"


def searxng_url() -> str:
    """Base URL of the local SearXNG container (news search, replaces Google News)."""
    return os.getenv("SEARXNG_URL", "http://localhost:8888").rstrip("/")