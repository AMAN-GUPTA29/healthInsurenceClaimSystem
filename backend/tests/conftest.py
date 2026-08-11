"""
Shared pytest fixtures and configuration.
"""

from __future__ import annotations

import os
import pytest


def _clear_all_caches():
    """Clear all lru_cache singletons to ensure test isolation."""
    from app.config.settings import get_settings
    get_settings.cache_clear()
    try:
        from app.api.deps import _get_ai_provider_singleton
        _get_ai_provider_singleton.cache_clear()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def reset_settings_cache(monkeypatch):
    """
    Clear all lru_cache singletons before each test so that
    individual tests can patch env vars and get a fresh Settings.
    Also removes any stray env vars that could bleed between tests.
    """
    # Remove vars that could bleed from the environment
    for var in ("APP_ENV", "AI_PROVIDER", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DATABASE_URL",
                "AI_MODEL", "LOG_LEVEL", "AI_TIMEOUT_SECONDS"):
        monkeypatch.delenv(var, raising=False)

    _clear_all_caches()
    yield
    _clear_all_caches()


@pytest.fixture
def test_settings():
    """Return a Settings instance configured for testing."""
    from app.config.settings import Settings
    return Settings(
        app_env="testing",
        database_url="sqlite+aiosqlite:///./data/test_claims.db",
        ai_provider="gemini",
        ai_model="gemini-2.5-flash",
        gemini_api_key="test-key-not-real",
        log_level="DEBUG",
        debug=True,
    )
