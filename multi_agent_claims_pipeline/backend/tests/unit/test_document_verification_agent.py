"""
Unit tests for DocumentVerificationAgent.

Uses pre-supplied DocumentClassification objects (the "fixture" path) so
these tests don't need a real/mocked AI provider — that path is exercised
separately (see test_document_verification_agent_ai_path below, and the
live-API section of the phase report for the real-call verification).
"""

from __future__ import annotations

import pytest

from app.agents.document_verification_agent import DocumentVerificationAgent
from app.ai.schemas.ai_schemas import AIStructuredResponse
from app.domain.errors import ExtractionError
from app.domain.models import ClaimCategory, DocumentMetadata, DocumentQuality, DocumentType
from app.domain.verification import DocumentClassification, DocumentVerificationStatus
from app.policy.policy_repository import PolicyRepository


@pytest.fixture
def agent() -> DocumentVerificationAgent:
    return DocumentVerificationAgent(ai_provider=None, policy_repository=PolicyRepository())


@pytest.fixture
def anyio_backend():
    return "asyncio"


def classification(file_id, doc_type, quality=DocumentQuality.GOOD, patient_name=None):
    return DocumentClassification(
        file_id=file_id, document_type=doc_type, quality=quality, patient_name=patient_name,
        confidence=1.0, source="fixture",
    )


class TestCorrectDocuments:
    @pytest.mark.anyio
    async def test_all_required_documents_present_passes(self, agent):
        docs = [
            DocumentMetadata(file_id="F1", file_name="rx.jpg"),
            DocumentMetadata(file_id="F2", file_name="bill.jpg"),
        ]
        classifications = {
            "F1": classification("F1", DocumentType.PRESCRIPTION),
            "F2": classification("F2", DocumentType.HOSPITAL_BILL),
        }
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert result.status == DocumentVerificationStatus.PASS
        assert result.missing_documents == []


class TestMissingDocument:
    @pytest.mark.anyio
    async def test_missing_required_document_blocks(self, agent):
        docs = [DocumentMetadata(file_id="F1", file_name="rx1.jpg"), DocumentMetadata(file_id="F2", file_name="rx2.jpg")]
        classifications = {
            "F1": classification("F1", DocumentType.PRESCRIPTION),
            "F2": classification("F2", DocumentType.PRESCRIPTION),
        }
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert result.status == DocumentVerificationStatus.BLOCKED
        assert result.missing_documents == [DocumentType.HOSPITAL_BILL]

    @pytest.mark.anyio
    async def test_message_names_uploaded_and_required_types(self, agent):
        docs = [DocumentMetadata(file_id="F1", file_name="rx1.jpg")]
        classifications = {"F1": classification("F1", DocumentType.PRESCRIPTION)}
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert "Prescription" in result.user_message
        assert "Hospital Bill" in result.user_message


class TestWrongDocument:
    @pytest.mark.anyio
    async def test_document_outside_required_and_optional_is_flagged_wrong(self, agent):
        docs = [
            DocumentMetadata(file_id="F1", file_name="rx.jpg"),
            DocumentMetadata(file_id="F2", file_name="bill.jpg"),
            DocumentMetadata(file_id="F3", file_name="dental.jpg"),
        ]
        classifications = {
            "F1": classification("F1", DocumentType.PRESCRIPTION),
            "F2": classification("F2", DocumentType.HOSPITAL_BILL),
            "F3": classification("F3", DocumentType.DENTAL_REPORT),
        }
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        # All required docs present -> PASS overall, but the extraneous doc is still flagged.
        assert result.status == DocumentVerificationStatus.PASS
        assert DocumentType.DENTAL_REPORT in result.wrong_documents


class TestUnreadableDocument:
    @pytest.mark.anyio
    async def test_unreadable_document_needs_resubmission(self, agent):
        docs = [
            DocumentMetadata(file_id="F1", file_name="rx.jpg"),
            DocumentMetadata(file_id="F2", file_name="bill.jpg"),
        ]
        classifications = {
            "F1": classification("F1", DocumentType.PRESCRIPTION, quality=DocumentQuality.GOOD),
            "F2": classification("F2", DocumentType.PHARMACY_BILL, quality=DocumentQuality.UNREADABLE),
        }
        result = await agent.run(
            claim_category=ClaimCategory.PHARMACY, documents=docs, classifications=classifications
        )
        assert result.status == DocumentVerificationStatus.NEEDS_RESUBMISSION
        assert len(result.quality_issues) == 1
        assert result.quality_issues[0].file_id == "F2"

    @pytest.mark.anyio
    async def test_unreadable_message_identifies_the_specific_document(self, agent):
        docs = [DocumentMetadata(file_id="F2", file_name="bill.jpg")]
        classifications = {
            "F2": classification("F2", DocumentType.PHARMACY_BILL, quality=DocumentQuality.UNREADABLE)
        }
        result = await agent.run(
            claim_category=ClaimCategory.PHARMACY, documents=docs, classifications=classifications
        )
        assert "Pharmacy Bill" in result.user_message
        assert "re-upload" in result.user_message.lower()

    @pytest.mark.anyio
    async def test_partial_quality_also_needs_resubmission(self, agent):
        docs = [DocumentMetadata(file_id="F1", file_name="rx.jpg")]
        classifications = {"F1": classification("F1", DocumentType.PRESCRIPTION, quality=DocumentQuality.PARTIAL)}
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert result.status == DocumentVerificationStatus.NEEDS_RESUBMISSION

    @pytest.mark.anyio
    async def test_unreadable_is_not_treated_as_rejection(self, agent):
        """Explicit regression guard: unreadable != BLOCKED."""
        docs = [DocumentMetadata(file_id="F1", file_name="bill.jpg")]
        classifications = {"F1": classification("F1", DocumentType.PHARMACY_BILL, quality=DocumentQuality.UNREADABLE)}
        result = await agent.run(
            claim_category=ClaimCategory.PHARMACY, documents=docs, classifications=classifications
        )
        assert result.status != DocumentVerificationStatus.BLOCKED


class TestUnknownDocumentType:
    @pytest.mark.anyio
    async def test_unknown_type_treated_as_wrong_document(self, agent):
        docs = [
            DocumentMetadata(file_id="F1", file_name="rx.jpg"),
            DocumentMetadata(file_id="F2", file_name="bill.jpg"),
            DocumentMetadata(file_id="F3", file_name="mystery.jpg"),
        ]
        classifications = {
            "F1": classification("F1", DocumentType.PRESCRIPTION),
            "F2": classification("F2", DocumentType.HOSPITAL_BILL),
            "F3": classification("F3", DocumentType.UNKNOWN),
        }
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert DocumentType.UNKNOWN in result.wrong_documents


class _FakeAIProvider:
    """Minimal AIProvider double — no vendor SDK, just the interface shape
    DocumentVerificationAgent actually calls."""

    def __init__(self, structured_data: dict):
        self._data = structured_data
        self.calls = 0

    async def generate_structured(self, request):
        self.calls += 1
        return AIStructuredResponse(
            data=self._data, model="fake-model", provider="fake", raw_text=None
        )


class TestAIClassificationPath:
    """Covers documents with NO pre-supplied classification — the real,
    non-fixture path DocumentVerificationAgent takes for actual
    submissions. Uses a fake AIProvider double (not a vendor SDK)."""

    @pytest.mark.anyio
    async def test_calls_ai_provider_when_no_classification_supplied(self):
        fake_provider = _FakeAIProvider(
            {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.8}
        )
        agent = DocumentVerificationAgent(ai_provider=fake_provider, policy_repository=PolicyRepository())
        docs = [
            DocumentMetadata(file_id="F1", file_name="rx.jpg"),
            DocumentMetadata(file_id="F2", file_name="bill.jpg"),
        ]
        classifications = {"F2": classification("F2", DocumentType.HOSPITAL_BILL)}

        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )

        assert fake_provider.calls == 1  # only F1 needed classifying
        assert result.status == DocumentVerificationStatus.PASS
        ai_classified = next(c for c in result.classifications if c.file_id == "F1")
        assert ai_classified.source == "ai"
        assert ai_classified.document_type == DocumentType.PRESCRIPTION

    @pytest.mark.anyio
    async def test_malformed_ai_response_raises_extraction_error(self):
        fake_provider = _FakeAIProvider({"document_type": "NOT_A_REAL_TYPE", "quality": "GOOD", "confidence": 0.5})
        agent = DocumentVerificationAgent(ai_provider=fake_provider, policy_repository=PolicyRepository())
        docs = [DocumentMetadata(file_id="F1", file_name="rx.jpg")]

        with pytest.raises(ExtractionError):
            await agent.run(claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications={})


class TestConfidence:
    @pytest.mark.anyio
    async def test_overall_confidence_is_the_minimum_across_documents(self, agent):
        docs = [
            DocumentMetadata(file_id="F1", file_name="rx.jpg"),
            DocumentMetadata(file_id="F2", file_name="bill.jpg"),
        ]
        classifications = {
            "F1": DocumentClassification(file_id="F1", document_type=DocumentType.PRESCRIPTION, confidence=0.95),
            "F2": DocumentClassification(file_id="F2", document_type=DocumentType.HOSPITAL_BILL, confidence=0.6),
        }
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert result.confidence == 0.6

    @pytest.mark.anyio
    async def test_confidence_none_when_not_provided(self, agent):
        docs = [DocumentMetadata(file_id="F1", file_name="rx.jpg")]
        classifications = {
            "F1": DocumentClassification(file_id="F1", document_type=DocumentType.PRESCRIPTION, confidence=None)
        }
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert result.confidence is None
