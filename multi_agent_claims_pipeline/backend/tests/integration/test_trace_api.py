"""
Integration tests for the claim trace retrieval endpoint.

GET /api/v1/claims/{claim_id}/trace

Seeds trace events directly through TraceRepository/TraceService (the same
components a future pipeline will use), then verifies the API surfaces
them correctly — real behavior end-to-end, not a mocked repository.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

TEST_DB_PATH = "./data/test_trace_api.db"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(monkeypatch):
    """
    Async test client for the FastAPI app, with a dedicated test database.

    Note: httpx's AsyncClient + ASGITransport does not trigger FastAPI's
    lifespan (startup/shutdown) — that only runs under a real ASGI server
    (uvicorn) or an explicit lifespan manager. Phase 0's health check never
    surfaced this because it doesn't touch the database. The trace endpoint
    does, so the database is initialised explicitly here instead.
    """
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{TEST_DB_PATH}")

    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

    from app.config.settings import get_settings
    from app.api.deps import _get_ai_provider_singleton
    get_settings.cache_clear()
    _get_ai_provider_singleton.cache_clear()

    from app.repositories.database import close_database, init_database
    await init_database(f"sqlite+aiosqlite:///{TEST_DB_PATH}")

    from app.main import create_app
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    await close_database()
    get_settings.cache_clear()
    _get_ai_provider_singleton.cache_clear()
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


async def _seed_events(claim_id: str) -> str:
    """Record a small realistic sequence of events for a claim. Returns trace_id."""
    from app.domain.trace import TraceComponent, TraceContext
    from app.repositories.trace_repository import TraceRepository
    from app.tracing.service import TraceService

    repo = TraceRepository()
    context = TraceContext.new(claim_id=claim_id)
    tracer = TraceService(context, sink=repo)

    await tracer.started(TraceComponent.CLAIM_VALIDATION, "validating submission")
    await tracer.completed(TraceComponent.CLAIM_VALIDATION, message="valid", duration_ms=12.5)
    async with tracer.span(TraceComponent.DOCUMENT_VERIFICATION):
        pass
    await tracer.completed(
        TraceComponent.DOCUMENT_EXTRACTION,
        confidence=0.91,
        metadata={"document_type": "PRESCRIPTION", "quality": "GOOD"},
    )
    return context.trace_id


@pytest.mark.anyio
async def test_trace_endpoint_returns_200(client):
    await _seed_events("CLM-API01")
    response = await client.get("/api/v1/claims/CLM-API01/trace")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_trace_endpoint_returns_claim_id_and_count(client):
    await _seed_events("CLM-API02")
    response = await client.get("/api/v1/claims/CLM-API02/trace")
    data = response.json()
    assert data["claim_id"] == "CLM-API02"
    assert data["count"] == 5


@pytest.mark.anyio
async def test_trace_endpoint_events_in_chronological_order(client):
    await _seed_events("CLM-API03")
    response = await client.get("/api/v1/claims/CLM-API03/trace")
    data = response.json()
    event_types = [e["event_type"] for e in data["events"]]
    assert event_types == ["STARTED", "COMPLETED", "STARTED", "COMPLETED", "COMPLETED"]

    components = [e["component"] for e in data["events"]]
    assert components == [
        "CLAIM_VALIDATION",
        "CLAIM_VALIDATION",
        "DOCUMENT_VERIFICATION",
        "DOCUMENT_VERIFICATION",
        "DOCUMENT_EXTRACTION",
    ]


@pytest.mark.anyio
async def test_trace_endpoint_includes_confidence_and_metadata(client):
    await _seed_events("CLM-API04")
    response = await client.get("/api/v1/claims/CLM-API04/trace")
    data = response.json()
    extraction_event = next(e for e in data["events"] if e["component"] == "DOCUMENT_EXTRACTION")
    assert extraction_event["confidence"] == 0.91
    assert extraction_event["metadata"] == {"document_type": "PRESCRIPTION", "quality": "GOOD"}


@pytest.mark.anyio
async def test_trace_endpoint_unknown_claim_returns_empty(client):
    response = await client.get("/api/v1/claims/CLM-DOES-NOT-EXIST/trace")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 0
    assert data["events"] == []


@pytest.mark.anyio
async def test_trace_endpoint_does_not_leak_other_claims(client):
    await _seed_events("CLM-API05")
    await _seed_events("CLM-API06")

    response = await client.get("/api/v1/claims/CLM-API05/trace")
    data = response.json()
    assert all(e["claim_id"] == "CLM-API05" for e in data["events"])
