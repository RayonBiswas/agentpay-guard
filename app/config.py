"""Runtime settings, read from environment variables (and .env if present)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional
    pass

APP_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    paypal_mode: str          # "sim" | "sandbox"
    paypal_client_id: str
    paypal_client_secret: str
    llm_base_url: str         # empty -> heuristic parsers only
    llm_model: str
    llm_api_key: str
    db_path: str
    policy_timezone: str
    catalog_path: str


def get_settings() -> Settings:
    """Read env at call time so tests can override variables."""
    return Settings(
        paypal_mode=os.getenv("PAYPAL_MODE", "sim"),
        paypal_client_id=os.getenv("PAYPAL_CLIENT_ID", ""),
        paypal_client_secret=os.getenv("PAYPAL_CLIENT_SECRET", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", ""),
        llm_model=os.getenv("LLM_MODEL", "llama3.2:3b"),
        llm_api_key=os.getenv("LLM_API_KEY", "ollama"),
        db_path=os.getenv("DB_PATH", "agentpay.db"),
        policy_timezone=os.getenv("POLICY_TIMEZONE", "Asia/Kolkata"),
        catalog_path=os.getenv("CATALOG_PATH", str(APP_DIR / "data" / "catalog.json")),
    )
