"""Unit tests for CrossDocumentValidationAgent."""

from __future__ import annotations

from datetime import date

import pytest

from app.agents.cross_document_validation_agent import CrossDocumentValidationAgent
from app.domain.models import DocumentType, Member, RelationshipType
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


def member(name: str, member_id: str = "EMP001") -> Member:
    return Member(
        member_id=member_id,
        name=name,
        date_of_birth=date(1985, 3, 15),
        gender="M",
        relationship=RelationshipType.SELF,
        join_date=date(2024, 4, 1),
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


class TestNoLegibleNameGapFix:
    """No-legible-name gap fix: a real classification pass that found zero
    patient names anywhere used to silently PASS with no identity check at
    all — not just "no conflict", genuinely nothing was verified. A claim
    whose documents belong to someone else, with a name too degraded for
    the AI to read, would have sailed through undetected as long as no
    other document disagreed. This must now BLOCK when the member is
    known; evaluation fixtures are exempt (see module docstring)."""

    @pytest.mark.anyio
    async def test_no_names_from_real_classification_blocks_when_member_known(self, agent):
        result = await agent.run(
            [
                DocumentClassification(
                    file_id="F1", document_type=DocumentType.PRESCRIPTION, patient_name=None, source="ai"
                ),
                DocumentClassification(
                    file_id="F2", document_type=DocumentType.HOSPITAL_BILL, patient_name=None, source="ai"
                ),
            ],
            member=member("Rajesh Kumar", member_id="EMP001"),
        )
        assert result.status == CrossDocumentValidationStatus.BLOCKED
        assert "Rajesh Kumar" in result.user_message
        assert "EMP001" in result.user_message

    @pytest.mark.anyio
    async def test_no_names_from_real_classification_still_passes_without_a_member(self, agent):
        """No member resolved (e.g. an unrecognised member_id would already
        have blocked earlier at ClaimValidationAgent, but this agent has no
        way to know that) — same "skip, don't assume" convention as every
        other member-gated check in this agent."""
        result = await agent.run(
            [
                DocumentClassification(
                    file_id="F1", document_type=DocumentType.PRESCRIPTION, patient_name=None, source="ai"
                ),
            ]
        )
        assert result.status == CrossDocumentValidationStatus.PASS

    @pytest.mark.anyio
    async def test_no_names_from_evaluation_fixture_still_passes_even_with_member_known(self, agent):
        """The official evaluation's fixture adapter (source="fixture")
        deliberately doesn't populate patient_name_on_doc for most test
        cases — that's the fixture's own scope, not a real "AI looked and
        found nothing" signal, so it must stay exempt from the new check."""
        result = await agent.run(
            [
                DocumentClassification(
                    file_id="F1", document_type=DocumentType.PRESCRIPTION, patient_name=None, source="fixture"
                ),
                DocumentClassification(
                    file_id="F2", document_type=DocumentType.HOSPITAL_BILL, patient_name=None, source="fixture"
                ),
            ],
            member=member("Rajesh Kumar", member_id="EMP001"),
        )
        assert result.status == CrossDocumentValidationStatus.PASS


class TestOmittedMemberIsFullyBackwardCompatible:
    """No existing caller passes `member=` — confirms the parameter is
    genuinely optional and changes nothing when absent (evaluation runner,
    every pre-fix test)."""

    @pytest.mark.anyio
    async def test_documents_agreeing_with_each_other_still_pass_with_no_member_supplied(self, agent):
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, "Vikram Joshi"),
                classified(DocumentType.HOSPITAL_BILL, "Vikram Joshi"),
            ]
        )
        assert result.status == CrossDocumentValidationStatus.PASS


class TestDocumentMemberIdentityMismatch:
    """Phase 2A identity-validation gap fix. Regression cases A-E as
    specified: same wrong person, correct member, mixed identities,
    case/whitespace normalization, and the pre-existing doc-to-doc
    mismatch (must not regress)."""

    @pytest.mark.anyio
    async def test_a_same_wrong_person_across_both_documents_blocks(self, agent):
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, "Vikram Joshi"),
                classified(DocumentType.HOSPITAL_BILL, "Vikram Joshi"),
            ],
            member=member("Rajesh Kumar"),
        )
        assert result.status == CrossDocumentValidationStatus.BLOCKED

    @pytest.mark.anyio
    async def test_a_message_names_both_the_detected_and_expected_identity(self, agent):
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, "Vikram Joshi"),
                classified(DocumentType.HOSPITAL_BILL, "Vikram Joshi"),
            ],
            member=member("Rajesh Kumar", member_id="EMP001"),
        )
        assert "Vikram Joshi" in result.user_message
        assert "Rajesh Kumar" in result.user_message
        assert "EMP001" in result.user_message
        # Not a generic message — must not just say "patient mismatch".
        assert result.user_message.lower() != "patient mismatch"

    @pytest.mark.anyio
    async def test_b_correct_member_across_both_documents_passes(self, agent):
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, "Rajesh Kumar"),
                classified(DocumentType.HOSPITAL_BILL, "Rajesh Kumar"),
            ],
            member=member("Rajesh Kumar"),
        )
        assert result.status == CrossDocumentValidationStatus.PASS

    @pytest.mark.anyio
    async def test_c_one_correct_one_wrong_document_blocks(self, agent):
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, "Rajesh Kumar"),
                classified(DocumentType.HOSPITAL_BILL, "Vikram Joshi"),
            ],
            member=member("Rajesh Kumar"),
        )
        assert result.status == CrossDocumentValidationStatus.BLOCKED

    @pytest.mark.anyio
    async def test_d_case_and_whitespace_normalization_still_passes(self, agent):
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, " rajesh kumar "),
                classified(DocumentType.HOSPITAL_BILL, "RAJESH KUMAR"),
            ],
            member=member("Rajesh Kumar"),
        )
        assert result.status == CrossDocumentValidationStatus.PASS

    @pytest.mark.anyio
    async def test_e_existing_document_to_document_mismatch_still_blocks_with_member_supplied(self, agent):
        """Regression guard: adding the member check must not change the
        existing doc-to-doc mismatch outcome or message when a member is
        also supplied — TC003's exact scenario, plus a member."""
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, "Rajesh Kumar"),
                classified(DocumentType.HOSPITAL_BILL, "Arjun Mehta"),
            ],
            member=member("Rajesh Kumar"),
        )
        assert result.status == CrossDocumentValidationStatus.BLOCKED
        assert "Rajesh Kumar" in result.user_message
        assert "Arjun Mehta" in result.user_message

    @pytest.mark.anyio
    async def test_single_named_document_mismatching_member_also_blocks(self, agent):
        """Logical extension beyond the 2-document examples: a lone named
        document that disagrees with the member is just as real a
        mismatch as two agreeing wrong documents — not required to wait
        for a second document to catch it."""
        result = await agent.run(
            [classified(DocumentType.PRESCRIPTION, "Vikram Joshi")],
            member=member("Rajesh Kumar"),
        )
        assert result.status == CrossDocumentValidationStatus.BLOCKED
        assert "Vikram Joshi" in result.user_message
        assert "Rajesh Kumar" in result.user_message

    @pytest.mark.anyio
    async def test_dependent_member_identity_checked_against_their_own_name_not_primary(self, agent):
        """member_id can resolve to a dependent (e.g. DEP001) whose own
        name differs from the primary member's — the check must compare
        against whichever Member was actually resolved, not assume SELF."""
        result = await agent.run(
            [
                classified(DocumentType.PRESCRIPTION, "Sunita Kumar"),
                classified(DocumentType.HOSPITAL_BILL, "Sunita Kumar"),
            ],
            member=member("Sunita Kumar", member_id="DEP001"),
        )
        assert result.status == CrossDocumentValidationStatus.PASS
