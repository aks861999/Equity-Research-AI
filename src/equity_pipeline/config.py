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
    }


def llm_settings() -> dict:
    """Only needed once the future summarization/sentiment stage is wired up."""
    return {
        "base_url": os.getenv("LLM_BASE_URL", "https://fhgenie.fraunhofer.de/v1"),
        "api_key": os.getenv("MANAGED_LLM_API_KEY", ""),
        "model": os.getenv("LLM_MODEL", "MiniMaxAI/MiniMax-M2.5"),
    }


def gnews_settings() -> dict:
    return {
        "api_key": os.getenv("GNEWS_API_KEY", "")
    }