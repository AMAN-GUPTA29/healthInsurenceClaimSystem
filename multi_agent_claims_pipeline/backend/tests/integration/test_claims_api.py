"""
Integration tests for POST /api/v1/claims and GET /api/v1/claims/{claim_id}.

Runs against a real (temporary) SQLite database and the real pipeline —
no mocked agents. AI calls aren't exercised here because every test uses
fixture-shaped documents (actual_type provided), same as the assignment's
own test cases; the AI-classification path is covered in
tests/unit/test_document_verification_agent.py.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

TEST_DB_PATH = "./data/test_claims_api.db"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(monkeypatch):
    """
    See tests/integration/test_trace_api.py for why the database is
    initialised explicitly here rather than relying on FastAPI's lifespan
    (AsyncClient + ASGITransport doesn't trigger it).
    """
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{TEST_DB_PATH}")

    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

    from app.config.settings import get_settings
    from app.api.deps import _get_ai_provider_singleton, _get_policy_repository_singleton
    get_settings.cache_clear()
    _get_ai_provider_singleton.cache_clear()
    _get_policy_repository_singleton.cache_clear()

    from app.repositories.database import close_database, init_database
    await init_database(f"sqlite+aiosqlite:///{TEST_DB_PATH}")

    from app.main import create_app
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    await close_database()
    get_settings.cache_clear()
    _get_ai_provider_singleton.cache_clear()
    _get_policy_repository_singleton.cache_clear()
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


TC001_PAYLOAD = {
    "member_id": "EMP001",
    "policy_id": "PLUM_GHI_2024",
    "claim_category": "CONSULTATION",
    "treatment_date": "2024-11-01",
    "claimed_amount": 1500,
    "documents": [
        {"file_id": "F001", "file_name": "dr_sharma_prescription.jpg", "actual_type": "PRESCRIPTION"},
        {"file_id": "F002", "file_name": "another_prescription.jpg", "actual_type": "PRESCRIPTION"},
    ],
}


@pytest.mark.anyio
async def test_submit_claim_returns_201(client):
    response = await client.post("/api/v1/claims", json=TC001_PAYLOAD)
    assert response.status_code == 201


@pytest.mark.anyio
async def test_submit_claim_tc001_blocks_with_actionable_message(client):
    response = await client.post("/api/v1/claims", json=TC001_PAYLOAD)
    data = response.json()
    assert data["status"] == "BLOCKED"
    assert data["stopped_at"] == "DOCUMENT_VERIFICATION"
    assert "hospital bill" in data["user_message"].lower()
    assert data["cross_document_validation_result"] is None


@pytest.mark.anyio
async def test_submit_claim_rejects_invalid_member(client):
    payload = {**TC001_PAYLOAD, "member_id": "EMP999"}
    response = await client.post("/api/v1/claims", json=payload)
    data = response.json()
    assert data["status"] == "BLOCKED"
    assert data["stopped_at"] == "CLAIM_VALIDATION"
    assert data["document_verification_result"] is None


@pytest.mark.anyio
async def test_submit_claim_rejects_amount_below_minimum(client):
    payload = {**TC001_PAYLOAD, "claimed_amount": 10}
    response = await client.post("/api/v1/claims", json=payload)
    data = response.json()
    assert data["status"] == "BLOCKED"
    assert any(e["code"] == "BELOW_MINIMUM_AMOUNT" for e in data["validation_result"]["errors"])


@pytest.mark.anyio
async def test_submit_claim_that_clears_phase_2a(client):
    payload = {
        "member_id": "EMP001",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "CONSULTATION",
        "treatment_date": "2024-11-01",
        "claimed_amount": 1500,
        "documents": [
            {"file_id": "F007", "file_name": "rx.jpg", "actual_type": "PRESCRIPTION", "patient_name_on_doc": "Rajesh Kumar"},
            {"file_id": "F008", "file_name": "bill.jpg", "actual_type": "HOSPITAL_BILL", "patient_name_on_doc": "Rajesh Kumar"},
        ],
    }
    response = await client.post("/api/v1/claims", json=payload)
    data = response.json()
    assert data["status"] == "PROCESSING"
    assert data["stopped_at"] is None
    assert data["document_verification_result"]["status"] == "PASS"
    assert data["cross_document_validation_result"]["status"] == "PASS"


@pytest.mark.anyio
async def test_get_claim_returns_previously_submitted_claim(client):
    submit_response = await client.post("/api/v1/claims", json=TC001_PAYLOAD)
    claim_id = submit_response.json()["claim_id"]

    get_response = await client.get(f"/api/v1/claims/{claim_id}")
    assert get_response.status_code == 200
    data = get_response.json()
    assert data["claim_id"] == claim_id
    assert data["status"] == "BLOCKED"
    assert len(data["documents"]) == 2


@pytest.mark.anyio
async def test_get_claim_includes_document_classifications(client):
    submit_response = await client.post("/api/v1/claims", json=TC001_PAYLOAD)
    claim_id = submit_response.json()["claim_id"]

    get_response = await client.get(f"/api/v1/claims/{claim_id}")
    data = get_response.json()
    assert all(d["document_type"] == "PRESCRIPTION" for d in data["documents"])


@pytest.mark.anyio
async def test_get_unknown_claim_returns_404(client):
    response = await client.get("/api/v1/claims/CLM-DOES-NOT-EXIST")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_submitted_claim_trace_is_retrievable_via_trace_endpoint(client):
    submit_response = await client.post("/api/v1/claims", json=TC001_PAYLOAD)
    claim_id = submit_response.json()["claim_id"]
    trace_id = submit_response.json()["trace_id"]

    trace_response = await client.get(f"/api/v1/claims/{claim_id}/trace")
    data = trace_response.json()
    assert data["count"] > 0
    assert all(e["trace_id"] == trace_id for e in data["events"])
    assert any(e["component"] == "PIPELINE" and e["event_type"] == "WARNING" for e in data["events"])


@pytest.mark.anyio
async def test_existing_health_endpoint_still_works(client):
    """Backward-compatibility guard (Phase 2A must not break Phase 0/1)."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
