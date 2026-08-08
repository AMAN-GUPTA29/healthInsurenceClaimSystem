"""
Unit tests for domain model validation.

Tests that:
- Domain models parse and validate correctly
- Enumerations work as expected
- Invalid data raises validation errors
- Domain model semantics are correct
"""

from __future__ import annotations

import pytest
from datetime import date
from decimal import Decimal

from pydantic import ValidationError as PydanticValidationError

from app.domain.models import (
    Claim,
    ClaimCategory,
    ClaimSubmission,
    DecisionType,
    Document,
    DocumentMetadata,
    DocumentQuality,
    DocumentType,
    Member,
    RejectionReason,
    RelationshipType,
)
from app.domain.errors import (
    AITimeoutError,
    ClaimsSystemError,
    ComponentFailureError,
    DocumentUnreadableError,
    MemberNotFoundError,
)


class TestDocumentType:
    def test_all_expected_values_exist(self):
        assert DocumentType.PRESCRIPTION
        assert DocumentType.HOSPITAL_BILL
        assert DocumentType.LAB_REPORT
        assert DocumentType.PHARMACY_BILL
        assert DocumentType.DENTAL_REPORT
        assert DocumentType.DISCHARGE_SUMMARY

    def test_string_value(self):
        assert DocumentType.PRESCRIPTION.value == "PRESCRIPTION"


class TestClaimCategory:
    def test_all_categories_exist(self):
        expected = {"CONSULTATION", "DIAGNOSTIC", "PHARMACY", "DENTAL", "VISION", "ALTERNATIVE_MEDICINE"}
        actual = {c.value for c in ClaimCategory}
        assert expected == actual


class TestDecisionType:
    def test_all_decisions_exist(self):
        assert DecisionType.APPROVED
        assert DecisionType.PARTIAL
        assert DecisionType.REJECTED
        assert DecisionType.MANUAL_REVIEW
        assert DecisionType.PENDING


class TestMemberModel:
    def make_member(self, **overrides) -> Member:
        defaults = {
            "member_id": "EMP001",
            "name": "Rajesh Kumar",
            "date_of_birth": date(1985, 3, 15),
            "gender": "M",
            "relationship": RelationshipType.SELF,
            "join_date": date(2024, 4, 1),
        }
        defaults.update(overrides)
        return Member(**defaults)

    def test_member_creation(self):
        member = self.make_member()
        assert member.member_id == "EMP001"
        assert member.name == "Rajesh Kumar"

    def test_is_primary_for_self(self):
        member = self.make_member(relationship=RelationshipType.SELF)
        assert member.is_primary is True

    def test_is_not_primary_for_dependent(self):
        member = self.make_member(
            relationship=RelationshipType.SPOUSE,
            primary_member_id="EMP001",
        )
        assert member.is_primary is False

    def test_age_calculation(self):
        # Fixed DOB for deterministic test
        member = self.make_member(date_of_birth=date(1990, 1, 1))
        # Age should be at least 34 (born 1990, current year >= 2024)
        assert member.age >= 34


class TestDocumentMetadata:
    def test_basic_creation(self):
        meta = DocumentMetadata(
            file_id="F001",
            file_name="prescription.jpg",
        )
        assert meta.file_id == "F001"
        assert meta.quality == DocumentQuality.UNKNOWN

    def test_declared_type(self):
        meta = DocumentMetadata(
            file_id="F002",
            file_name="bill.pdf",
            declared_type=DocumentType.HOSPITAL_BILL,
        )
        assert meta.declared_type == DocumentType.HOSPITAL_BILL


class TestDocument:
    def test_effective_type_prefers_detected(self):
        meta = DocumentMetadata(
            file_id="F001",
            file_name="test.jpg",
            declared_type=DocumentType.PRESCRIPTION,
            detected_type=DocumentType.HOSPITAL_BILL,
        )
        doc = Document(metadata=meta)
        assert doc.effective_type == DocumentType.HOSPITAL_BILL

    def test_effective_type_falls_back_to_declared(self):
        meta = DocumentMetadata(
            file_id="F001",
            file_name="test.jpg",
            declared_type=DocumentType.PRESCRIPTION,
        )
        doc = Document(metadata=meta)
        assert doc.effective_type == DocumentType.PRESCRIPTION


class TestClaimSubmission:
    def make_submission(self, **overrides) -> ClaimSubmission:
        defaults = {
            "member_id": "EMP001",
            "policy_id": "PLUM_GHI_2024",
            "claim_category": ClaimCategory.CONSULTATION,
            "treatment_date": date(2024, 11, 1),
            "claimed_amount": Decimal("1500"),
            "documents": [
                DocumentMetadata(file_id="F001", file_name="rx.jpg")
            ],
        }
        defaults.update(overrides)
        return ClaimSubmission(**defaults)

    def test_valid_submission(self):
        s = self.make_submission()
        assert s.member_id == "EMP001"
        assert s.claimed_amount == Decimal("1500")

    def test_future_treatment_date_rejected(self):
        with pytest.raises(PydanticValidationError):
            self.make_submission(treatment_date=date(2099, 1, 1))

    def test_zero_amount_rejected(self):
        with pytest.raises(PydanticValidationError):
            self.make_submission(claimed_amount=Decimal("0"))

    def test_negative_amount_rejected(self):
        with pytest.raises(PydanticValidationError):
            self.make_submission(claimed_amount=Decimal("-100"))

    def test_simulate_component_failure_defaults_false(self):
        s = self.make_submission()
        assert s.simulate_component_failure is False


class TestClaim:
    def test_claim_id_auto_generated(self):
        submission = ClaimSubmission(
            member_id="EMP001",
            policy_id="PLUM_GHI_2024",
            claim_category=ClaimCategory.CONSULTATION,
            treatment_date=date(2024, 11, 1),
            claimed_amount=Decimal("1500"),
            documents=[DocumentMetadata(file_id="F001", file_name="rx.jpg")],
        )
        claim = Claim(submission=submission)
        assert claim.claim_id.startswith("CLM-")

    def test_documents_populated_from_submission(self):
        submission = ClaimSubmission(
            member_id="EMP001",
            policy_id="PLUM_GHI_2024",
            claim_category=ClaimCategory.CONSULTATION,
            treatment_date=date(2024, 11, 1),
            claimed_amount=Decimal("1500"),
            documents=[
                DocumentMetadata(file_id="F001", file_name="rx.jpg"),
                DocumentMetadata(file_id="F002", file_name="bill.jpg"),
            ],
        )
        claim = Claim(submission=submission)
        assert len(claim.documents) == 2
        assert claim.documents[0].file_id == "F001"


class TestDomainErrors:
    def test_claims_system_error_is_base(self):
        err = ClaimsSystemError("test error")
        assert isinstance(err, Exception)
        assert err.code == "CLAIMS_SYSTEM_ERROR"
        assert err.message == "test error"

    def test_member_not_found_error(self):
        err = MemberNotFoundError("EMP999")
        assert isinstance(err, ClaimsSystemError)
        assert err.code == "MEMBER_NOT_FOUND"
        assert "EMP999" in err.details.values()

    def test_document_unreadable_error_is_recoverable(self):
        err = DocumentUnreadableError("F001", "too blurry")
        assert err.recoverable is True

    def test_component_failure_error_is_recoverable(self):
        err = ComponentFailureError("ExtractionAgent", "timeout")
        assert err.recoverable is True
        assert "ExtractionAgent" in err.details["component"]

    def test_ai_timeout_error(self):
        err = AITimeoutError("anthropic", 60)
        assert isinstance(err, ClaimsSystemError)
        assert err.details["timeout_seconds"] == 60
