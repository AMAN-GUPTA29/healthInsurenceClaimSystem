"""
Integration tests for the health endpoint.

Tests that:
- GET /api/v1/health returns 200
- Response matches expected schema
- Response includes AI provider info
- Response includes version and environment
"""

from __future__ import annotations

import os
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(monkeypatch):
    """Async test client for the FastAPI app."""
    # Set env vars for this test
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/test_health.db")

    # Clear all caches so settings + provider are re-created from new env vars
    from app.config.settings import get_settings
    from app.api.deps import _get_ai_provider_singleton
    get_settings.cache_clear()
    _get_ai_provider_singleton.cache_clear()

    from app.main import create_app
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    # Cleanup
    get_settings.cache_clear()
    _get_ai_provider_singleton.cache_clear()


@pytest.mark.anyio
async def test_health_endpoint_returns_200(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_health_response_schema(client):
    response = await client.get("/api/v1/health")
    data = response.json()

    # Required fields
    assert "status" in data
    assert "version" in data
    assert "environment" in data
    assert "timestamp" in data
    assert "ai_provider" in data
    assert "database" in data


@pytest.mark.anyio
async def test_health_status_is_healthy(client):
    response = await client.get("/api/v1/health")
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.anyio
async def test_health_includes_ai_provider_info(client):
    response = await client.get("/api/v1/health")
    data = response.json()
    ai = data["ai_provider"]

    assert "provider" in ai
    assert "model" in ai
    assert "status" in ai
    assert ai["provider"] == "anthropic"


@pytest.mark.anyio
async def test_root_endpoint_returns_service_info(client):
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert "health" in data


@pytest.fixture
async def gemini_client(monkeypatch):
    """Async test client for the FastAPI app, configured for Gemini."""
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/test_health.db")

    from app.config.settings import get_settings
    from app.api.deps import _get_ai_provider_singleton
    get_settings.cache_clear()
    _get_ai_provider_singleton.cache_clear()

    from app.main import create_app
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    get_settings.cache_clear()
    _get_ai_provider_singleton.cache_clear()


@pytest.mark.anyio
async def test_health_reports_gemini_as_configured(gemini_client):
    """Regression test: health status must reflect whichever provider is active,
    not always check anthropic_api_key."""
    response = await gemini_client.get("/api/v1/health")
    data = response.json()
    ai = data["ai_provider"]

    assert ai["provider"] == "gemini"
    assert ai["status"] == "configured"
