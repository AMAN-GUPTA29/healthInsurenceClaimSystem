"""
FastAPI Dependency Injection.

This module provides FastAPI-compatible dependency functions that construct
and inject shared resources into route handlers.

The AI provider is a singleton constructed from settings and shared
across all request handlers within a process. Agents receive it via
constructor injection, never by importing a module-level client.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.ai.providers.base import AIProvider
from app.ai.providers.factory import create_ai_provider
from app.config.settings import Settings, get_settings


# ── Settings ──────────────────────────────────────────────────────────────────


def get_settings_dep() -> Settings:
    """FastAPI dependency that returns the application settings singleton."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


# ── AI Provider ───────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _get_ai_provider_singleton() -> AIProvider:
    """
    Create and cache the AI provider singleton.
    The provider is initialised (async) during app lifespan, not here.
    """
    return create_ai_provider(get_settings())


def get_ai_provider() -> AIProvider:
    """
    FastAPI dependency that returns the configured AI provider.
    Must be initialised before use (done in app lifespan).
    """
    return _get_ai_provider_singleton()


AIProviderDep = Annotated[AIProvider, Depends(get_ai_provider)]
