"""Unit tests for CrossDocumentValidationAgent."""

from __future__ import annotations

import pytest

from app.agents.cross_document_validation_agent import CrossDocumentValidationAgent
from app.domain.models import DocumentType
from app.domain.verification import CrossDocumentValidationStatus, DocumentClassification


@pytest.fixture
def agent() -> CrossDocumentValidationAgent:
    return CrossDocumentValidationAgent()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def classified(doc_type, patient_name, confidence=1.0):
    return DocumentClassification(
        file_id=doc_type.value, document_type=doc_type, patient_name=patient_name, confidence=confidence
    )


class TestMatchingPatients:
    @pytest.mark.anyio
    async def test_same_name_passes(self, agent):
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, "Rajesh Kumar"),
                classified(DocumentType.HOSPITAL_BILL, "Rajesh Kumar"),
            ]
        )
        assert result.status == CrossDocumentValidationStatus.PASS

    @pytest.mark.anyio
    async def test_name_matching_is_case_and_whitespace_insensitive(self, agent):
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, "  rajesh   kumar "),
                classified(DocumentType.HOSPITAL_BILL, "Rajesh Kumar"),
            ]
        )
        assert result.status == CrossDocumentValidationStatus.PASS


class TestMismatchingPatients:
    @pytest.mark.anyio
    async def test_different_names_blocks(self, agent):
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, "Rajesh Kumar"),
                classified(DocumentType.HOSPITAL_BILL, "Arjun Mehta"),
            ]
        )
        assert result.status == CrossDocumentValidationStatus.BLOCKED

    @pytest.mark.anyio
    async def test_mismatch_message_names_both_patients(self, agent):
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, "Rajesh Kumar"),
                classified(DocumentType.HOSPITAL_BILL, "Arjun Mehta"),
            ]
        )
        assert "Rajesh Kumar" in result.user_message
        assert "Arjun Mehta" in result.user_message

    @pytest.mark.anyio
    async def test_mismatch_result_carries_structured_patient_names(self, agent):
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, "Rajesh Kumar"),
                classified(DocumentType.HOSPITAL_BILL, "Arjun Mehta"),
            ]
        )
        assert result.patient_names == {
            "Prescription": "Rajesh Kumar",
            "Hospital Bill": "Arjun Mehta",
        }


class TestMissingPatientIdentity:
    @pytest.mark.anyio
    async def test_no_named_documents_passes_without_asserting_match(self, agent):
        result = await agent.run(
            [
                DocumentClassification(file_id="F1", document_type=DocumentType.PRESCRIPTION, patient_name=None),
                DocumentClassification(file_id="F2", document_type=DocumentType.HOSPITAL_BILL, patient_name=None),
            ]
        )
        assert result.status == CrossDocumentValidationStatus.PASS

    @pytest.mark.anyio
    async def test_single_named_document_passes(self, agent):
        result = await agent.run([classified(DocumentType.PRESCRIPTION, "Rajesh Kumar")])
        assert result.status == CrossDocumentValidationStatus.PASS
