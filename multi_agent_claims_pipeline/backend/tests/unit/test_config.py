"""
Unit tests for configuration loading.

Tests that:
- Settings load from environment variables
- Defaults are correct
- AI provider settings parse correctly
- CORS origins parse correctly
"""

from __future__ import annotations

import os
import pytest

from app.config.settings import AIProvider, Environment, LogLevel, Settings, get_settings


class TestSettingsDefaults:
    def test_default_app_env_is_development(self):
        s = Settings()
        assert s.app_env == Environment.DEVELOPMENT

    def test_default_ai_provider_is_gemini(self):
        s = Settings()
        assert s.ai_provider == AIProvider.GEMINI

    def test_default_ai_timeout(self):
        s = Settings()
        assert s.ai_timeout_seconds == 60

    def test_default_database_url_is_sqlite(self):
        s = Settings()
        assert "sqlite" in s.database_url

    def test_default_cors_includes_vite_port(self):
        s = Settings()
        assert "http://localhost:5173" in s.cors_origins

    def test_default_log_level_is_info(self):
        s = Settings()
        assert s.log_level == LogLevel.INFO

    def test_default_ai_temperature(self):
        s = Settings()
        assert 0.0 <= s.ai_temperature <= 2.0

    def test_version_is_set(self):
        s = Settings()
        assert s.app_version  # non-empty


class TestSettingsFromEnv:
    def test_ai_provider_from_env(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "anthropic")
        get_settings.cache_clear()
        s = Settings()
        assert s.ai_provider == AIProvider.ANTHROPIC

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-abc123")
        get_settings.cache_clear()
        s = Settings()
        assert s.anthropic_api_key == "sk-test-abc123"

    def test_log_level_from_env(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        get_settings.cache_clear()
        s = Settings()
        assert s.log_level == LogLevel.DEBUG

    def test_ai_model_from_env(self, monkeypatch):
        monkeypatch.setenv("AI_MODEL", "claude-opus-4-5")
        get_settings.cache_clear()
        s = Settings()
        assert s.ai_model == "claude-opus-4-5"


class TestSettingsProperties:
    def test_is_production_false_by_default(self):
        s = Settings(app_env="development")
        assert not s.is_production

    def test_is_production_true(self):
        s = Settings(app_env="production")
        assert s.is_production

    def test_is_testing_true(self):
        s = Settings(app_env="testing")
        assert s.is_testing

    def test_is_testing_false_by_default(self):
        s = Settings(app_env="development")
        assert not s.is_testing


class TestSettingsSingleton:
    def test_get_settings_returns_same_instance(self):
        a = get_settings()
        b = get_settings()
        assert a is b

    def test_cache_clear_returns_fresh_instance(self):
        a = get_settings()
        get_settings.cache_clear()
        b = get_settings()
        # Should be equal but not necessarily same object after cache clear
        assert a.app_name == b.app_name
