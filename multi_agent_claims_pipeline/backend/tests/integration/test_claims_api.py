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

# Minimal valid Phase 2B extraction responses (every field the AI-facing
# schema requires — app/ai/prompts/*_extraction.py — must be present, even
# as an empty sentinel; the agent does not tolerate a missing key). Appended
# after classification responses for any test whose claim reaches
# DOCUMENT_EXTRACTION (i.e. clears document + cross-document validation) —
# DocumentExtractionAgent makes one more analyze_document call per
# extractable document, consumed by the same _FakeSequentialAIProvider.
PRESCRIPTION_EXTRACTION_RESPONSE = {
    "patient_name": "", "patient_age": "", "patient_gender": "", "patient_date_of_birth": "",
    "prescription_date": "", "doctor_name": "", "doctor_registration_number": "",
    "doctor_specialization": "", "doctor_hospital_or_clinic": "", "diagnosis": "", "treatment": "",
    "medications": [], "investigations": [], "signature_present": "UNCLEAR", "stamp_present": "UNCLEAR",
    "confidence": 0.85, "warnings": [], "evidence": [],
}
HOSPITAL_BILL_EXTRACTION_RESPONSE = {
    "patient_name": "", "hospital_name": "", "bill_number": "", "bill_date": "",
    "admission_date": "", "discharge_date": "", "doctor_name": "", "doctor_registration_number": "",
    "line_items": [], "subtotal": "", "discount": "", "tax": "", "total": "", "currency": "INR",
    "confidence": 0.85, "warnings": [], "evidence": [],
}
LAB_REPORT_EXTRACTION_RESPONSE = {
    "patient_name": "", "patient_age": "", "patient_gender": "", "referring_doctor": "",
    "sample_date": "", "report_date": "", "tests": [], "laboratory_name": "",
    "pathologist_name": "", "registration_number": "",
    "confidence": 0.85, "warnings": [], "evidence": [],
}


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
        # Extraction stage (Phase 2B) runs next, one more analyze_document
        # call per document, in the same order.
        PRESCRIPTION_EXTRACTION_RESPONSE,
        HOSPITAL_BILL_EXTRACTION_RESPONSE,
    ]
    client = await client_factory(ai_responses)
    response = await client.post("/api/v1/claims", data=data, files=files)
    result = response.json()
    assert result["status"] == "DECIDED"
    assert result["stopped_at"] is None
    assert result["document_verification_result"]["status"] == "PASS"
    assert result["cross_document_validation_result"]["status"] == "PASS"
    assert result["extraction_result"] is not None
    assert len(result["extraction_result"]["extractions"]) == 2
    assert result["extraction_result"]["has_failures"] is False
    doc_extractions = {d["file_id"]: d["extraction"] for d in result["documents"]}
    assert all(doc_extractions.values())  # every document has its extraction attached too

    # Phase 2C: PolicyEngine/FinancialCalculationService/FraudAnalysisAgent
    # make no AI calls, so they run automatically over real HTTP once
    # extraction succeeds — api/deps.py always wires them into the pipeline.
    assert result["policy_evaluation_result"] is not None
    assert result["policy_evaluation_result"]["coverage_category"] == "CONSULTATION"
    assert result["financial_calculation_result"] is not None
    assert result["financial_calculation_result"]["payable_amount"] is not None
    assert result["fraud_analysis_result"] is not None
    assert result["fraud_analysis_result"]["risk_level"] in ("LOW", "MEDIUM", "HIGH")

    # Phase 2D: clean, fully-covered consultation claim within every limit —
    # DecisionGenerationAgent should reach APPROVED with the exact payable
    # amount FinancialCalculationService computed (1500 - 10% copay = 1350),
    # never a value the LLM could have invented.
    assert result["decision"] is not None
    assert result["decision"]["decision"] == "APPROVED"
    assert result["decision"]["approved_amount"] == result["financial_calculation_result"]["payable_amount"]
    # Not a strict high-confidence bound: this fixture's hospital bill has
    # no hospital_name (empty-sentinel extraction response), so PolicyEngine
    # itself legitimately caps its own confidence at 0.6 (NETWORK_HOSPITAL
    # WARNING — hospital identity unknown, never assumed either way), which
    # DecisionGenerationAgent's min()-of-available-scores strategy correctly
    # propagates. The real thing under test is that this is still >= the
    # low-confidence/MANUAL_REVIEW threshold, i.e. still a normal APPROVED.
    assert result["decision"]["confidence_score"] >= 0.5
    assert result["decision"]["explanation"]
    assert result["decision"]["member_facing_message"]
    # ExplanationAgent's real AI call fails in this test (the fake provider
    # only implements analyze_document, not generate_structured) — that
    # must degrade to a deterministic fallback, never crash the request or
    # blank out the explanation fields.
    assert result["decision"]["explanation_detail"]["source"] == "FALLBACK"
    assert result["decision"]["explanation_detail"]["degraded"] is True


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
async def test_submit_claim_member_identity_mismatch_blocks(client_factory):
    """
    Phase 2A identity-validation gap fix, HTTP-level regression — see
    docs/AI_HANDOFF.md 'Phase 2A identity-validation gap fixed'. EMP001
    resolves to Rajesh Kumar; both uploaded documents are internally
    consistent with each other (both "Vikram Joshi") but belong to neither
    the member nor each other's expected identity. Before the fix this
    incorrectly returned status=PROCESSING.
    """
    data = {
        "member_id": "EMP001",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "CONSULTATION",
        "treatment_date": "2024-11-01",
        "claimed_amount": "1500",
    }
    files = [
        ("documents", ("prescription_vikram.jpg", JPEG_BYTES, "image/jpeg")),
        ("documents", ("bill_vikram.jpg", JPEG_BYTES, "image/jpeg")),
    ]
    ai_responses = [
        {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "Vikram Joshi", "confidence": 0.95},
        {"document_type": "HOSPITAL_BILL", "quality": "GOOD", "patient_name": "Vikram Joshi", "confidence": 0.93},
    ]
    client = await client_factory(ai_responses)

    response = await client.post("/api/v1/claims", data=data, files=files)
    assert response.status_code == 201  # succeeds at the API level — a business BLOCKED, not an HTTP error
    result = response.json()

    assert result["status"] == "BLOCKED"
    assert result["stopped_at"] == "CROSS_DOCUMENT_VALIDATION"
    assert "Vikram Joshi" in result["user_message"]
    assert "Rajesh Kumar" in result["user_message"]
    assert result["cross_document_validation_result"]["status"] == "BLOCKED"

    # Phase 2C regression guard (assignment section 36): Policy/Financial/
    # Fraud must never run for a claim that stopped at Phase 2A.
    assert result["policy_evaluation_result"] is None
    assert result["financial_calculation_result"] is None
    assert result["fraud_analysis_result"] is None
    # Phase 2D: a BLOCKED claim never gets a fake decision — processing
    # status = BLOCKED, final decision = null, per the explicit distinction
    # required for early-stop cases (see docs/architecture.md).
    assert result["decision"] is None

    claim_id = result["claim_id"]
    trace_response = await client.get(f"/api/v1/claims/{claim_id}/trace")
    trace = trace_response.json()
    cross_doc_events = [e for e in trace["events"] if e["component"] == "CROSS_DOCUMENT_VALIDATION"]
    assert len(cross_doc_events) >= 1
    completed = next(e for e in cross_doc_events if e["event_type"] == "COMPLETED")
    assert completed["metadata"]["status"] == "BLOCKED"
    assert completed["metadata"]["expected_member_name"] == "Rajesh Kumar"

    policy_events = [e for e in trace["events"] if e["component"] == "POLICY_ENGINE"]
    assert len(policy_events) == 1
    assert policy_events[0]["event_type"] == "SKIPPED"
    financial_events = [e for e in trace["events"] if e["component"] == "FINANCIAL_CALCULATION"]
    assert financial_events[0]["event_type"] == "SKIPPED"
    fraud_events = [e for e in trace["events"] if e["component"] == "FRAUD_ANALYSIS"]
    assert fraud_events[0]["event_type"] == "SKIPPED"
    decision_events = [e for e in trace["events"] if e["component"] == "DECISION_GENERATION"]
    assert decision_events[0]["event_type"] == "SKIPPED"
    explanation_events = [e for e in trace["events"] if e["component"] == "EXPLANATION"]
    assert explanation_events[0]["event_type"] == "SKIPPED"


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
        # Extraction stage (Phase 2B) — one more analyze_document call per
        # document, same order as classification.
        PRESCRIPTION_EXTRACTION_RESPONSE,
        LAB_REPORT_EXTRACTION_RESPONSE,
        HOSPITAL_BILL_EXTRACTION_RESPONSE,
    ]
    client = await client_factory(ai_responses)
    response = await client.post("/api/v1/claims", data=data, files=files)
    result = response.json()
    assert response.status_code == 201
    assert len(result["documents"]) == 3
    assert result["member_id"] == "EMP001"
    assert result["claim_category"] == "DIAGNOSTIC"
    assert result["status"] == "DECIDED"  # Phase 2D: claim reaches a final decision, not just PROCESSING
    assert len(result["extraction_result"]["extractions"]) == 3


@pytest.mark.anyio
async def test_extraction_result_survives_a_database_round_trip(client_factory):
    """
    Regression guard for restart persistence (docs/AI_HANDOFF.md Phase 2B
    "Restart Persistence"): POST's response is built from the in-memory
    Claim the pipeline just produced, which would pass even if
    ClaimRepository never persisted extraction correctly. A separate GET —
    a fresh ClaimRepository.get_by_id() call, exercising
    _extraction_result_from_rows' rehydration from
    ClaimDocumentORM.extraction_json — is what actually proves persistence.
    """
    data = {
        "member_id": "EMP001",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "CONSULTATION",
        "treatment_date": "2024-11-01",
        "claimed_amount": "1500",
    }
    files = [
        ("documents", ("rx.jpg", JPEG_BYTES, "image/jpeg")),
        ("documents", ("bill.jpg", JPEG_BYTES, "image/jpeg")),
    ]
    ai_responses = [
        {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "Rajesh Kumar", "confidence": 0.94},
        {"document_type": "HOSPITAL_BILL", "quality": "GOOD", "patient_name": "Rajesh Kumar", "confidence": 0.91},
        {
            **PRESCRIPTION_EXTRACTION_RESPONSE,
            "patient_name": "Rajesh Kumar",
            "diagnosis": "Viral Fever",
            "doctor_name": "Dr. Arun Sharma",
            "confidence": 0.88,
        },
        {**HOSPITAL_BILL_EXTRACTION_RESPONSE, "hospital_name": "City Clinic", "total": "1500.00", "confidence": 0.9},
    ]
    client = await client_factory(ai_responses)
    submit_response = await client.post("/api/v1/claims", data=data, files=files)
    claim_id = submit_response.json()["claim_id"]

    get_response = await client.get(f"/api/v1/claims/{claim_id}")
    assert get_response.status_code == 200
    result = get_response.json()

    assert result["extraction_result"] is not None
    assert len(result["extraction_result"]["extractions"]) == 2
    assert result["extraction_result"]["has_failures"] is False

    rx_doc = next(d for d in result["documents"] if d["document_type"] == "PRESCRIPTION")
    assert rx_doc["extraction"]["extraction"]["diagnosis"] == "Viral Fever"
    assert rx_doc["extraction"]["extraction"]["doctor"]["name"] == "Dr. Arun Sharma"
    assert rx_doc["extraction"]["patient"]["name"] == "Rajesh Kumar"

    bill_doc = next(d for d in result["documents"] if d["document_type"] == "HOSPITAL_BILL")
    assert bill_doc["extraction"]["extraction"]["hospital_name"] == "City Clinic"
    assert bill_doc["extraction"]["extraction"]["total"] == "1500.00"
    assert "storage_reference" not in bill_doc


@pytest.mark.anyio
async def test_policy_financial_fraud_results_survive_a_database_round_trip(client_factory):
    """
    Restart-persistence guard for Phase 2C/2D, mirroring
    test_extraction_result_survives_a_database_round_trip above: the POST
    response is built from the in-memory Claim the pipeline just produced,
    which would pass even if ClaimRepository never persisted
    policy_evaluation_result_json/financial_calculation_result_json/
    fraud_analysis_result_json/decision_json correctly. A separate GET — a
    fresh ClaimRepository.get_by_id() call, exercising _to_domain()'s
    rehydration of these JSON columns — is what actually proves the
    results survive a server restart.
    """
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
        PRESCRIPTION_EXTRACTION_RESPONSE,
        HOSPITAL_BILL_EXTRACTION_RESPONSE,
    ]
    client = await client_factory(ai_responses)
    submit_response = await client.post("/api/v1/claims", data=data, files=files)
    submitted = submit_response.json()
    claim_id = submitted["claim_id"]

    get_response = await client.get(f"/api/v1/claims/{claim_id}")
    assert get_response.status_code == 200
    result = get_response.json()

    assert result["policy_evaluation_result"] is not None
    assert result["policy_evaluation_result"] == submitted["policy_evaluation_result"]
    assert result["policy_evaluation_result"]["coverage_category"] == "CONSULTATION"

    assert result["financial_calculation_result"] is not None
    assert result["financial_calculation_result"] == submitted["financial_calculation_result"]
    assert result["financial_calculation_result"]["payable_amount"] is not None

    assert result["fraud_analysis_result"] is not None
    assert result["fraud_analysis_result"] == submitted["fraud_analysis_result"]
    assert result["fraud_analysis_result"]["risk_level"] in ("LOW", "MEDIUM", "HIGH")

    # Phase 2D: the decision (including its nested explanation_detail)
    # must round-trip exactly too.
    assert result["decision"] is not None
    assert result["decision"] == submitted["decision"]
    assert result["decision"]["decision"] in ("APPROVED", "PARTIAL", "REJECTED", "MANUAL_REVIEW")
    assert result["decision"]["explanation_detail"] is not None


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
