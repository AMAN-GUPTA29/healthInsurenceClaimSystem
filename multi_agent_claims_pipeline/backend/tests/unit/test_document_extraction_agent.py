"""
Unit tests for DocumentExtractionAgent.

Uses fake AIProvider/DocumentStorage doubles (no vendor SDK, no real
filesystem I/O) — same convention as
tests/unit/test_document_verification_agent.py. Real-Gemini verification is
manual (see docs/AI_HANDOFF.md Phase 2B "Real AI Verification").
"""

from __future__ import annotations

import pytest

from app.agents.document_extraction_agent import DocumentExtractionAgent
from app.ai.schemas.ai_schemas import DocumentAnalysisResponse
from app.domain.errors import AITimeoutError
from app.domain.extraction import HospitalBillExtraction, PrescriptionExtraction
from app.domain.models import DocumentMetadata, DocumentQuality, DocumentType
from app.storage.document_storage import DocumentStorage


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeDocumentStorage(DocumentStorage):
    def __init__(self, contents: dict[str, bytes]):
        self._contents = contents
        self.read_refs: list[str] = []

    async def save(self, *, claim_id: str, filename: str, content: bytes) -> str:
        raise NotImplementedError("not needed for these tests")

    async def read(self, storage_reference: str) -> bytes:
        self.read_refs.append(storage_reference)
        if storage_reference not in self._contents:
            raise FileNotFoundError(storage_reference)
        return self._contents[storage_reference]


class _FakeAnalyzeDocumentProvider:
    """AIProvider double implementing only analyze_document (extraction is
    always real-multimodal — see DocumentExtractionAgent's docstring)."""

    def __init__(self, responses_by_call: list):
        # Each entry: a dict (structured_data), an Exception instance, or None
        self._responses = list(responses_by_call)
        self.calls = 0
        self.requests = []

    async def analyze_document(self, request):
        self.calls += 1
        self.requests.append(request)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return DocumentAnalysisResponse(structured_data=response, model="fake-vision-model", provider="fake")


# Flat shape matching PRESCRIPTION_EXTRACTION_SCHEMA
# (app/ai/prompts/prescription_extraction.py) — this is what the AI
# provider actually returns; DocumentExtractionAgent's adapter reshapes it
# into the nested PrescriptionExtraction domain model (patient/doctor
# sub-objects) — see _adapt_prescription.
PRESCRIPTION_DATA = {
    "patient_name": "Rajesh Kumar",
    "patient_age": "39",
    "patient_gender": "M",
    "patient_date_of_birth": "",
    "prescription_date": "2024-11-01",
    "doctor_name": "Dr. Arun Sharma",
    "doctor_registration_number": "KA/45678/2015",
    "doctor_specialization": "",
    "doctor_hospital_or_clinic": "",
    "diagnosis": "Viral Fever",
    "treatment": "",
    "medications": [{"name": "Paracetamol", "strength": "650mg", "dosage": "1-1-1", "frequency": "", "duration": "5 days", "route": "", "instructions": ""}],
    "investigations": ["CBC", "Dengue NS1"],
    "signature_present": "YES",
    "stamp_present": "YES",
    "confidence": 0.94,
    "warnings": [],
    "evidence": [{"field": "diagnosis", "quote": "Diagnosis: Viral Fever"}],
}

# Flat shape matching HOSPITAL_BILL_EXTRACTION_SCHEMA
# (app/ai/prompts/hospital_bill_extraction.py).
HOSPITAL_BILL_DATA = {
    "patient_name": "Rajesh Kumar",
    "hospital_name": "City Clinic",
    "bill_number": "CMC/2024/08321",
    "bill_date": "2024-11-01",
    "admission_date": "",
    "discharge_date": "",
    "doctor_name": "Dr. Arun Sharma",
    "doctor_registration_number": "",
    "line_items": [{"description": "Consultation Fee", "quantity": "1", "unit_price": "1000.00", "amount": "1000.00"}],
    "subtotal": "1000.00",
    "discount": "",
    "tax": "",
    "total": "1000.00",
    "currency": "INR",
    "confidence": 0.91,
    "warnings": [],
    "evidence": [],
}


def _doc(file_id, doc_type, storage_ref="ref", mime="image/jpeg", quality=DocumentQuality.GOOD):
    return DocumentMetadata(
        file_id=file_id,
        file_name=f"{file_id}.jpg",
        mime_type=mime,
        storage_reference=storage_ref,
        detected_type=doc_type,
        quality=quality,
    )


class TestSuccessfulExtraction:
    @pytest.mark.anyio
    async def test_extracts_prescription(self):
        storage = _FakeDocumentStorage({"ref": b"\xff\xd8\xff-jpeg"})
        provider = _FakeAnalyzeDocumentProvider([PRESCRIPTION_DATA])
        agent = DocumentExtractionAgent(ai_provider=provider, document_storage=storage)

        result = await agent.run(documents=[_doc("F007", DocumentType.PRESCRIPTION)])

        assert len(result.extractions) == 1
        envelope = result.extractions[0]
        assert envelope.file_id == "F007"
        assert isinstance(envelope.extraction, PrescriptionExtraction)
        assert envelope.extraction.diagnosis == "Viral Fever"
        assert envelope.patient.name == "Rajesh Kumar"
        assert envelope.document_date is not None
        assert result.failures == []
        assert result.has_failures is False
        assert result.confidence == 0.94
        assert len(result.ai_calls) == 1
        assert result.ai_calls[0].provider == "fake"

    @pytest.mark.anyio
    async def test_extracts_multiple_documents_of_different_types(self):
        storage = _FakeDocumentStorage({"ref1": b"\xff\xd8\xff-a", "ref2": b"\xff\xd8\xff-b"})
        provider = _FakeAnalyzeDocumentProvider([PRESCRIPTION_DATA, HOSPITAL_BILL_DATA])
        agent = DocumentExtractionAgent(ai_provider=provider, document_storage=storage)

        result = await agent.run(
            documents=[
                _doc("F007", DocumentType.PRESCRIPTION, storage_ref="ref1"),
                _doc("F008", DocumentType.HOSPITAL_BILL, storage_ref="ref2"),
            ]
        )

        assert {e.file_id for e in result.extractions} == {"F007", "F008"}
        bill = next(e for e in result.extractions if e.file_id == "F008")
        assert isinstance(bill.extraction, HospitalBillExtraction)
        assert bill.extraction.total.__class__.__name__ == "Decimal"

    @pytest.mark.anyio
    async def test_correct_schema_selected_per_document_type(self):
        """The right prompt/schema builder must be chosen from the
        document's classified type — never a one-size-fits-all schema."""
        storage = _FakeDocumentStorage({"ref": b"\xff\xd8\xff"})
        provider = _FakeAnalyzeDocumentProvider([HOSPITAL_BILL_DATA])
        agent = DocumentExtractionAgent(ai_provider=provider, document_storage=storage)

        await agent.run(documents=[_doc("F008", DocumentType.HOSPITAL_BILL)])

        assert provider.requests[0].metadata.get("prompt_version") == "2b.1"
        # A hospital-bill-specific field must be present in the schema sent to the AI.
        assert "line_items" in provider.requests[0].output_schema["properties"]


class TestUnsupportedAndFailedExtraction:
    @pytest.mark.anyio
    async def test_unsupported_document_type_is_skipped_not_failed(self):
        storage = _FakeDocumentStorage({})
        provider = _FakeAnalyzeDocumentProvider([])
        agent = DocumentExtractionAgent(ai_provider=provider, document_storage=storage)

        result = await agent.run(documents=[_doc("F099", DocumentType.DIAGNOSTIC_REPORT)])

        assert result.skipped == ["F099"]
        assert result.failures == []
        assert result.extractions == []
        assert provider.calls == 0

    @pytest.mark.anyio
    async def test_unknown_document_type_is_skipped(self):
        storage = _FakeDocumentStorage({})
        provider = _FakeAnalyzeDocumentProvider([])
        agent = DocumentExtractionAgent(ai_provider=provider, document_storage=storage)

        result = await agent.run(documents=[_doc("F099", DocumentType.UNKNOWN)])

        assert result.skipped == ["F099"]

    @pytest.mark.anyio
    async def test_missing_storage_reference_is_a_per_document_failure_not_a_crash(self):
        storage = _FakeDocumentStorage({})
        provider = _FakeAnalyzeDocumentProvider([])
        agent = DocumentExtractionAgent(ai_provider=provider, document_storage=storage)
        doc = _doc("F007", DocumentType.PRESCRIPTION, storage_ref=None)

        result = await agent.run(documents=[doc])

        assert result.extractions == []
        assert len(result.failures) == 1
        assert result.failures[0].file_id == "F007"
        assert result.has_failures is True

    @pytest.mark.anyio
    async def test_provider_exception_is_isolated_to_one_document(self):
        """One document's AI call failing (timeout, auth, etc.) must not
        stop extraction of the others — see agent docstring."""
        storage = _FakeDocumentStorage({"ref1": b"\xff\xd8\xff-a", "ref2": b"\xff\xd8\xff-b"})
        provider = _FakeAnalyzeDocumentProvider([AITimeoutError("fake", 60), PRESCRIPTION_DATA])
        agent = DocumentExtractionAgent(ai_provider=provider, document_storage=storage)

        result = await agent.run(
            documents=[
                _doc("F007", DocumentType.PRESCRIPTION, storage_ref="ref1"),
                _doc("F008", DocumentType.PRESCRIPTION, storage_ref="ref2"),
            ]
        )

        assert len(result.extractions) == 1
        assert result.extractions[0].file_id == "F008"
        assert len(result.failures) == 1
        assert result.failures[0].file_id == "F007"
        assert result.has_failures is True

    @pytest.mark.anyio
    async def test_malformed_structured_response_becomes_a_failure(self):
        storage = _FakeDocumentStorage({"ref": b"\xff\xd8\xff"})
        provider = _FakeAnalyzeDocumentProvider([{"confidence": "not-a-number"}])
        agent = DocumentExtractionAgent(ai_provider=provider, document_storage=storage)

        result = await agent.run(documents=[_doc("F007", DocumentType.PRESCRIPTION)])

        assert result.extractions == []
        assert len(result.failures) == 1
        assert result.failures[0].file_id == "F007"

    @pytest.mark.anyio
    async def test_empty_structured_response_becomes_a_failure(self):
        storage = _FakeDocumentStorage({"ref": b"\xff\xd8\xff"})
        provider = _FakeAnalyzeDocumentProvider([None])
        agent = DocumentExtractionAgent(ai_provider=provider, document_storage=storage)

        result = await agent.run(documents=[_doc("F007", DocumentType.PRESCRIPTION)])

        assert len(result.failures) == 1

    @pytest.mark.anyio
    async def test_run_never_raises_even_when_every_document_fails(self):
        storage = _FakeDocumentStorage({})
        provider = _FakeAnalyzeDocumentProvider([AITimeoutError("fake", 60)])
        agent = DocumentExtractionAgent(ai_provider=provider, document_storage=storage)

        result = await agent.run(documents=[_doc("F007", DocumentType.PRESCRIPTION, storage_ref="missing-ref")])

        assert result.has_failures is True
        assert result.confidence is None


class TestConfidenceAggregation:
    @pytest.mark.anyio
    async def test_overall_confidence_is_minimum_across_successful_extractions(self):
        low_confidence_data = {**PRESCRIPTION_DATA, "confidence": 0.4}
        storage = _FakeDocumentStorage({"ref1": b"a", "ref2": b"b"})
        provider = _FakeAnalyzeDocumentProvider([PRESCRIPTION_DATA, low_confidence_data])
        agent = DocumentExtractionAgent(ai_provider=provider, document_storage=storage)

        result = await agent.run(
            documents=[
                _doc("F1", DocumentType.PRESCRIPTION, storage_ref="ref1"),
                _doc("F2", DocumentType.PRESCRIPTION, storage_ref="ref2"),
            ]
        )

        assert result.confidence == 0.4

    @pytest.mark.anyio
    async def test_confidence_none_when_no_documents_extracted(self):
        storage = _FakeDocumentStorage({})
        provider = _FakeAnalyzeDocumentProvider([])
        agent = DocumentExtractionAgent(ai_provider=provider, document_storage=storage)

        result = await agent.run(documents=[_doc("F1", DocumentType.DIAGNOSTIC_REPORT)])

        assert result.confidence is None
