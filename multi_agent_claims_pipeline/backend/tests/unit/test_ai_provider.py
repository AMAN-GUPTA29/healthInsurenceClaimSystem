"""
Unit tests for the AI provider abstraction.

Tests that:
- AnthropicProvider implements AIProvider interface
- Provider factory creates correct provider
- Provider isolation is maintained (no vendor leakage)
- Error types are domain errors
"""

from __future__ import annotations

import pytest

from app.ai.providers.base import AIProvider
from app.ai.providers.factory import create_ai_provider
from app.config.settings import AIProvider as AIProviderEnum, Settings
from app.domain.errors import AIProviderNotConfiguredError


class TestAIProviderInterface:
    def test_provider_factory_returns_ai_provider_instance(self):
        """Factory must return an AIProvider subclass."""
        settings = Settings(
            ai_provider="anthropic",
            ai_model="claude-haiku-3-5",
            anthropic_api_key="test-key",
        )
        provider = create_ai_provider(settings)
        assert isinstance(provider, AIProvider)

    def test_provider_name_is_anthropic(self):
        settings = Settings(
            ai_provider="anthropic",
            ai_model="claude-haiku-3-5",
            anthropic_api_key="test-key",
        )
        provider = create_ai_provider(settings)
        assert provider.provider_name == "anthropic"

    def test_model_name_matches_config(self):
        settings = Settings(
            ai_provider="anthropic",
            ai_model="claude-opus-4-5",
            anthropic_api_key="test-key",
        )
        provider = create_ai_provider(settings)
        assert provider.model_name == "claude-opus-4-5"

    def test_factory_raises_for_unconfigured_gemini(self):
        settings = Settings(ai_provider="gemini")
        with pytest.raises(AIProviderNotConfiguredError):
            create_ai_provider(settings)

    def test_factory_raises_for_unconfigured_openai(self):
        settings = Settings(ai_provider="openai")
        with pytest.raises(AIProviderNotConfiguredError):
            create_ai_provider(settings)

    def test_factory_raises_when_api_key_missing(self):
        """Anthropic provider requires an API key."""
        settings = Settings(
            ai_provider="anthropic",
            anthropic_api_key=None,
        )
        with pytest.raises(AIProviderNotConfiguredError):
            create_ai_provider(settings)

    def test_provider_supports_native_pdf(self):
        settings = Settings(
            ai_provider="anthropic",
            ai_model="claude-haiku-3-5",
            anthropic_api_key="test-key",
        )
        provider = create_ai_provider(settings)
        # Anthropic Claude 3+ supports PDF natively
        assert provider.supports_native_pdf is True

    def test_provider_supports_vision(self):
        settings = Settings(
            ai_provider="anthropic",
            ai_model="claude-haiku-3-5",
            anthropic_api_key="test-key",
        )
        provider = create_ai_provider(settings)
        assert provider.supports_vision is True


class TestAIProviderVendorIsolation:
    def test_anthropic_sdk_not_importable_from_domain(self):
        """
        Verify the domain layer doesn't have anthropic imported.
        This is a smoke test — if anthropic is in domain module's globals,
        the architecture rule has been violated.
        """
        import app.domain.models as models_module
        assert "anthropic" not in dir(models_module)

    def test_anthropic_sdk_not_importable_from_agents(self):
        import app.agents.base_agent as agent_module
        assert "anthropic" not in dir(agent_module)

    def test_anthropic_provider_is_only_file_with_sdk(self):
        """
        The anthropic SDK should only be imported in anthropic_provider.py.
        We verify by checking that importing it from the base provider doesn't
        expose anthropic symbols.
        """
        from app.ai.providers import base
        assert not hasattr(base, "anthropic")
        assert not hasattr(base, "AsyncAnthropic")
