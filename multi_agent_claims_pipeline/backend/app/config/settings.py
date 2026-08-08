"""
Application configuration.

All settings are loaded from environment variables (or a .env file).
Secrets must never be hardcoded here.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


class AIProvider(str, Enum):
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OPENAI = "openai"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """
    Typed application configuration loaded from environment variables.

    Each field maps to an env variable of the same name (uppercased).
    See .env.example for the full list of supported variables.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "Health Insurance Claims Processing System"
    app_version: str = "0.1.0"
    app_env: Environment = Environment.DEVELOPMENT
    debug: bool = False

    # ── Server ───────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/claims.db",
        description="SQLAlchemy async database URL. Default: SQLite. Override for PostgreSQL.",
    )

    # ── AI Provider ───────────────────────────────────────────────────────────
    ai_provider: AIProvider = AIProvider.ANTHROPIC
    ai_model: str = Field(
        default="claude-sonnet-4-5",
        description="Model identifier for the configured AI provider.",
    )
    ai_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    ai_max_tokens: int = Field(default=4096, ge=1)
    ai_timeout_seconds: int = Field(default=60, ge=1)

    # ── Anthropic ─────────────────────────────────────────────────────────────
    anthropic_api_key: Optional[str] = Field(
        default=None,
        description="Anthropic Claude API key. Required when ai_provider=anthropic.",
    )

    # ── Gemini (future) ───────────────────────────────────────────────────────
    gemini_api_key: Optional[str] = Field(default=None)

    # ── OpenAI (future) ───────────────────────────────────────────────────────
    openai_api_key: Optional[str] = Field(default=None)

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: LogLevel = LogLevel.INFO
    log_json: bool = Field(
        default=False,
        description="If True, emit structured JSON logs (useful for production/log aggregators).",
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: List[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"],
        description="Allowed CORS origins (frontend URLs).",
    )

    # ── Policy ────────────────────────────────────────────────────────────────
    policy_file_path: str = Field(
        default="policy_terms.json",
        description="Path to the policy terms JSON file (source of truth for all rules).",
    )

    # ── Fraud / Risk ──────────────────────────────────────────────────────────
    fraud_auto_review_threshold: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description="Fraud score above which a claim is routed to MANUAL_REVIEW.",
    )

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def warn_missing_anthropic_key(cls, v: Optional[str]) -> Optional[str]:
        # Validation-time warning; actual enforcement happens in the provider.
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == Environment.PRODUCTION

    @property
    def is_testing(self) -> bool:
        return self.app_env == Environment.TESTING


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the singleton Settings instance.
    Uses lru_cache so the .env file is read exactly once.
    Override in tests by calling get_settings.cache_clear() before patching.
    """
    return Settings()
