"""
Integration tests for POST /api/v1/claims (multipart/form-data, real file
uploads — Phase 2A correction) and GET /api/v1/claims/{claim_id}.

Runs against a real (temporary) SQLite database, real DocumentStorage (a
temp directory), and the real pipeline — no mocked agents. The AI provider
is overridden with a fake double (no vendor SDK, no network) via FastAPI's
dependency_overrides, matching this project's existing philosophy: never
depend on a real network call in the automated test suite (see
tests/unit/test_document_verification_agent.py). Real-AI verification is
done manually/live — see docs/AI_HANDOFF.md.
"""

from __future__ import annotations

import os
import shutil

import pytest
from httpx import ASGITransport, AsyncClient

from app.ai.schemas.ai_schemas import DocumentAnalysisResponse

TEST_DB_PATH = "./data/test_claims_api.db"
TEST_UPLOAD_DIR = "./data/test_claims_api_uploads"

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 100
PDF_BYTES = b"%PDF-1.4\n" + b"x" * 100


class _FakeSequentialAIProvider:
    """
    AIProvider double that returns pre-set structured classifications in
    upload order — DocumentVerificationAgent classifies documents
    sequentially, so this deterministically simulates "the AI looked at
    document N and returned X" without any real network call.
    """

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self._index = 0

    async def analyze_document(self, request):
        data = self._responses[self._index]
        self._index += 1
        return DocumentAnalysisResponse(structured_data=data, model="fake-vision-model", provider="fake")

    async def generate_structured(self, request):  # pragma: no cover
        raise AssertionError("real uploads must use analyze_document")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client_factory(monkeypatch):
    """
    Yields a factory: `await make_client(ai_responses)` -> AsyncClient,
    with the AI provider overridden to return `ai_responses` in order for
    successive analyze_document calls.
    """
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{TEST_DB_PATH}")
    monkeypatch.setenv("UPLOAD_DIR", TEST_UPLOAD_DIR)

    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)

    from app.config.settings import get_settings
    from app.api.deps import (
        _get_ai_provider_singleton,
        _get_policy_repository_singleton,
        _get_document_storage_singleton,
        get_ai_provider,
    )

    get_settings.cache_clear()
    _get_ai_provider_singleton.cache_clear()
    _get_policy_repository_singleton.cache_clear()
    _get_document_storage_singleton.cache_clear()

    from app.repositories.database import close_database, init_database

    await init_database(f"sqlite+aiosqlite:///{TEST_DB_PATH}")

    from app.main import create_app

    app = create_app()
    clients: list[AsyncClient] = []

    async def make_client(ai_responses: list[dict] | None = None) -> AsyncClient:
        if ai_responses is not None:
            fake_provider = _FakeSequentialAIProvider(ai_responses)
            app.dependency_overrides[get_ai_provider] = lambda: fake_provider
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(ac)
        return ac

    yield make_client

    for ac in clients:
        await ac.aclose()
    app.dependency_overrides.clear()
    await close_database()
    get_settings.cache_clear()
    _get_ai_provider_singleton.cache_clear()
    _get_policy_repository_singleton.cache_clear()
    _get_document_storage_singleton.cache_clear()
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)


def tc001_form():
    """Two prescriptions — TC001's real-document equivalent."""
    data = {
        "member_id": "EMP001",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "CONSULTATION",
        "treatment_date": "2024-11-01",
        "claimed_amount": "1500",
    }
    files = [
        ("documents", ("dr_sharma_prescription.jpg", JPEG_BYTES, "image/jpeg")),
        ("documents", ("another_prescription.jpg", JPEG_BYTES, "image/jpeg")),
    ]
    ai_responses = [
        {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
        {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
    ]
    return data, files, ai_responses


@pytest.mark.anyio
async def test_submit_claim_returns_201(client_factory):
    data, files, ai_responses = tc001_form()
    client = await client_factory(ai_responses)
    response = await client.post("/api/v1/claims", data=data, files=files)
    assert response.status_code == 201


@pytest.mark.anyio
async def test_submit_claim_tc001_blocks_with_actionable_message(client_factory):
    data, files, ai_responses = tc001_form()
    client = await client_factory(ai_responses)
    response = await client.post("/api/v1/claims", data=data, files=files)
    result = response.json()
    assert result["status"] == "BLOCKED"
    assert result["stopped_at"] == "DOCUMENT_VERIFICATION"
    assert "hospital bill" in result["user_message"].lower()
    assert result["cross_document_validation_result"] is None


@pytest.mark.anyio
async def test_submit_claim_rejects_invalid_member(client_factory):
    data, files, ai_responses = tc001_form()
    data["member_id"] = "EMP999"
    client = await client_factory(ai_responses)
    response = await client.post("/api/v1/claims", data=data, files=files)
    result = response.json()
    assert result["status"] == "BLOCKED"
    assert result["stopped_at"] == "CLAIM_VALIDATION"
    assert result["document_verification_result"] is None


@pytest.mark.anyio
async def test_submit_claim_rejects_amount_below_minimum(client_factory):
    data, files, ai_responses = tc001_form()
    data["claimed_amount"] = "10"
    client = await client_factory(ai_responses)
    response = await client.post("/api/v1/claims", data=data, files=files)
    result = response.json()
    assert result["status"] == "BLOCKED"
    assert any(e["code"] == "BELOW_MINIMUM_AMOUNT" for e in result["validation_result"]["errors"])


@pytest.mark.anyio
async def test_submit_claim_that_clears_phase_2a(client_factory):
    data = {
        "member_id": "EMP001",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "CONSULTATION",
        "treatment_date": "2024-11-01",
        "claimed_amount": "1500",
    }
    files = [
        ("documents", ("rx.jpg", JPEG_BYTES, "image/jpeg")),
        ("documents", ("bill.pdf", PDF_BYTES, "application/pdf")),
    ]
    ai_responses = [
        {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "Rajesh Kumar", "confidence": 0.94},
        {"document_type": "HOSPITAL_BILL", "quality": "GOOD", "patient_name": "Rajesh Kumar", "confidence": 0.91},
    ]
    client = await client_factory(ai_responses)
    response = await client.post("/api/v1/claims", data=data, files=files)
    result = response.json()
    assert result["status"] == "PROCESSING"
    assert result["stopped_at"] is None
    assert result["document_verification_result"]["status"] == "PASS"
    assert result["cross_document_validation_result"]["status"] == "PASS"


@pytest.mark.anyio
async def test_submit_claim_tc002_unreadable_requests_resubmission(client_factory):
    data = {
        "member_id": "EMP004",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "PHARMACY",
        "treatment_date": "2024-10-25",
        "claimed_amount": "800",
    }
    files = [
        ("documents", ("prescription.jpg", JPEG_BYTES, "image/jpeg")),
        ("documents", ("blurry_bill.jpg", JPEG_BYTES, "image/jpeg")),
    ]
    ai_responses = [
        {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
        {"document_type": "PHARMACY_BILL", "quality": "UNREADABLE", "patient_name": "", "confidence": 0.4},
    ]
    client = await client_factory(ai_responses)
    response = await client.post("/api/v1/claims", data=data, files=files)
    result = response.json()
    assert result["status"] == "DOCUMENTS_PENDING"
    assert result["document_verification_result"]["status"] == "NEEDS_RESUBMISSION"
    assert "pharmacy bill" in result["user_message"].lower()


@pytest.mark.anyio
async def test_submit_claim_tc003_different_patients_detected(client_factory):
    data = {
        "member_id": "EMP001",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "CONSULTATION",
        "treatment_date": "2024-11-01",
        "claimed_amount": "1500",
    }
    files = [
        ("documents", ("prescription_rajesh.jpg", JPEG_BYTES, "image/jpeg")),
        ("documents", ("bill_arjun.jpg", JPEG_BYTES, "image/jpeg")),
    ]
    ai_responses = [
        {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "Rajesh Kumar", "confidence": 0.93},
        {"document_type": "HOSPITAL_BILL", "quality": "GOOD", "patient_name": "Arjun Mehta", "confidence": 0.9},
    ]
    client = await client_factory(ai_responses)
    response = await client.post("/api/v1/claims", data=data, files=files)
    result = response.json()
    assert result["status"] == "BLOCKED"
    assert result["stopped_at"] == "CROSS_DOCUMENT_VALIDATION"
    assert "Rajesh Kumar" in result["user_message"]
    assert "Arjun Mehta" in result["user_message"]


@pytest.mark.anyio
async def test_submit_claim_rejects_unsupported_file_type(client_factory):
    data = {
        "member_id": "EMP001",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "CONSULTATION",
        "treatment_date": "2024-11-01",
        "claimed_amount": "1500",
    }
    files = [("documents", ("malware.exe", b"MZ-not-a-document", "application/x-msdownload"))]
    client = await client_factory([])
    response = await client.post("/api/v1/claims", data=data, files=files)
    assert response.status_code == 422
    assert response.json()["error"] == "UNSUPPORTED_DOCUMENT_TYPE"


@pytest.mark.anyio
async def test_submit_claim_with_no_documents_rejected(client_factory):
    data = {
        "member_id": "EMP001",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "CONSULTATION",
        "treatment_date": "2024-11-01",
        "claimed_amount": "1500",
    }
    client = await client_factory([])
    response = await client.post("/api/v1/claims", data=data, files=[])
    # FastAPI itself 422s when the required `documents` File(...) field is absent.
    assert response.status_code == 422


@pytest.mark.anyio
async def test_submit_claim_with_empty_file_rejected(client_factory):
    data = {
        "member_id": "EMP001",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "CONSULTATION",
        "treatment_date": "2024-11-01",
        "claimed_amount": "1500",
    }
    files = [("documents", ("empty.jpg", b"", "image/jpeg"))]
    client = await client_factory([])
    response = await client.post("/api/v1/claims", data=data, files=files)
    assert response.status_code == 422
    assert response.json()["error"] == "EMPTY_DOCUMENT"


@pytest.mark.anyio
async def test_submit_claim_multiple_documents_and_metadata(client_factory):
    """Section requirement: metadata + multiple files submitted together."""
    data = {
        "member_id": "EMP001",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "DIAGNOSTIC",
        "treatment_date": "2024-11-02",
        "claimed_amount": "5000",
    }
    files = [
        ("documents", ("rx.jpg", JPEG_BYTES, "image/jpeg")),
        ("documents", ("lab.pdf", PDF_BYTES, "application/pdf")),
        ("documents", ("bill.jpg", JPEG_BYTES, "image/jpeg")),
    ]
    ai_responses = [
        {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
        {"document_type": "LAB_REPORT", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
        {"document_type": "HOSPITAL_BILL", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
    ]
    client = await client_factory(ai_responses)
    response = await client.post("/api/v1/claims", data=data, files=files)
    result = response.json()
    assert response.status_code == 201
    assert len(result["documents"]) == 3
    assert result["member_id"] == "EMP001"
    assert result["claim_category"] == "DIAGNOSTIC"


@pytest.mark.anyio
async def test_get_claim_returns_previously_submitted_claim(client_factory):
    data, files, ai_responses = tc001_form()
    client = await client_factory(ai_responses)
    submit_response = await client.post("/api/v1/claims", data=data, files=files)
    claim_id = submit_response.json()["claim_id"]

    get_response = await client.get(f"/api/v1/claims/{claim_id}")
    assert get_response.status_code == 200
    result = get_response.json()
    assert result["claim_id"] == claim_id
    assert result["status"] == "BLOCKED"
    assert len(result["documents"]) == 2


@pytest.mark.anyio
async def test_get_claim_includes_document_classification_details(client_factory):
    data, files, ai_responses = tc001_form()
    client = await client_factory(ai_responses)
    submit_response = await client.post("/api/v1/claims", data=data, files=files)
    claim_id = submit_response.json()["claim_id"]

    get_response = await client.get(f"/api/v1/claims/{claim_id}")
    result = get_response.json()
    for doc in result["documents"]:
        assert doc["document_type"] == "PRESCRIPTION"
        assert doc["processing_status"] == "PROCESSED"
        assert doc["confidence"] == 0.9
        # storage_reference must never be exposed
        assert "storage_reference" not in doc


@pytest.mark.anyio
async def test_get_unknown_claim_returns_404(client_factory):
    client = await client_factory([])
    response = await client.get("/api/v1/claims/CLM-DOES-NOT-EXIST")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_submitted_claim_trace_includes_document_verification_events(client_factory):
    data, files, ai_responses = tc001_form()
    client = await client_factory(ai_responses)
    submit_response = await client.post("/api/v1/claims", data=data, files=files)
    claim_id = submit_response.json()["claim_id"]
    trace_id = submit_response.json()["trace_id"]

    trace_response = await client.get(f"/api/v1/claims/{claim_id}/trace")
    result = trace_response.json()
    assert result["count"] > 0
    assert all(e["trace_id"] == trace_id for e in result["events"])
    doc_verification_events = [e for e in result["events"] if e["component"] == "DOCUMENT_VERIFICATION"]
    assert len(doc_verification_events) >= 1
    completed = next(e for e in doc_verification_events if e["event_type"] == "COMPLETED")
    assert completed["ai_metadata"]["provider"] == "fake"


@pytest.mark.anyio
async def test_existing_health_endpoint_still_works(client_factory):
    """Backward-compatibility guard."""
    client = await client_factory([])
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
