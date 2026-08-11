"""
AI Provider Factory.

Constructs the appropriate AIProvider based on configuration.
This is the ONLY place (besides provider files) that knows about provider classes.
"""

from __future__ import annotations

from app.config.settings import AIProvider as AIProviderEnum, Settings
from app.ai.providers.base import AIProvider
from app.domain.errors import AIProviderNotConfiguredError


def create_ai_provider(settings: Settings) -> AIProvider:
    """
    Factory function to create an AIProvider based on application settings.

    Called once at application startup. The resulting provider instance is
    shared (injected) across all agents.

    Args:
        settings: Application settings with provider config.

    Returns:
        A configured (but not yet initialised) AIProvider instance.

    Raises:
        AIProviderNotConfiguredError: If the requested provider is not implemented.
        AIAuthenticationError: If required credentials are missing.
    """
    if settings.ai_provider == AIProviderEnum.ANTHROPIC:
        from app.ai.providers.anthropic_provider import AnthropicProvider

        if not settings.anthropic_api_key:
            raise AIProviderNotConfiguredError("anthropic")

        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.ai_model,
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
            timeout_seconds=settings.ai_timeout_seconds,
        )

    if settings.ai_provider == AIProviderEnum.GEMINI:
        from app.ai.providers.gemini_provider import GeminiProvider

        if not settings.gemini_api_key:
            raise AIProviderNotConfiguredError("gemini")

        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.ai_model,
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
            timeout_seconds=settings.ai_timeout_seconds,
        )

    # ── Future providers ──────────────────────────────────────────────────────
    if settings.ai_provider == AIProviderEnum.OPENAI:
        raise AIProviderNotConfiguredError("openai")

    raise AIProviderNotConfiguredError(settings.ai_provider.value)
