"""
Unit tests for the Phase 2A domain models (app/domain/verification.py) and
the Claim model's new Phase 2A fields.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.domain.models import Claim, ClaimCategory, ClaimStatus, ClaimSubmission, DocumentMetadata
from app.domain.verification import (
    CrossDocumentValidationResult,
    CrossDocumentValidationStatus,
    DocumentClassification,
    DocumentVerificationResult,
    DocumentVerificationStatus,
    ValidationIssue,
    ValidationResult,
)


class TestValidationResult:
    def test_valid_result_has_no_errors(self):
        result = ValidationResult(valid=True)
        assert result.errors == []
        assert result.warnings == []

    def test_issue_carries_structured_fields(self):
        issue = ValidationIssue(code="MEMBER_NOT_FOUND", message="not found", field="member_id", recoverable=False)
        assert issue.code == "MEMBER_NOT_FOUND"
        assert issue.field == "member_id"

    def test_field_is_optional(self):
        issue = ValidationIssue(code="X", message="msg")
        assert issue.field is None


class TestDocumentClassification:
    def test_confidence_bounds_enforced(self):
        with pytest.raises(PydanticValidationError):
            DocumentClassification(file_id="F1", document_type="PRESCRIPTION", confidence=1.5)

    def test_default_source_is_ai(self):
        c = DocumentClassification(file_id="F1", document_type="PRESCRIPTION")
        assert c.source == "ai"


class TestDocumentVerificationResult:
    def test_all_three_statuses_accepted(self):
        for status in DocumentVerificationStatus:
            result = DocumentVerificationResult(status=status)
            assert result.status == status

    def test_defaults_are_empty_not_none(self):
        result = DocumentVerificationResult(status=DocumentVerificationStatus.PASS)
        assert result.missing_documents == []
        assert result.wrong_documents == []
        assert result.quality_issues == []


class TestCrossDocumentValidationResult:
    def test_both_statuses_accepted(self):
        for status in CrossDocumentValidationStatus:
            result = CrossDocumentValidationResult(status=status)
            assert result.status == status

    def test_patient_names_default_empty_dict(self):
        result = CrossDocumentValidationResult(status=CrossDocumentValidationStatus.PASS)
        assert result.patient_names == {}


class TestClaimPhase2AFields:
    def make_claim(self) -> Claim:
        submission = ClaimSubmission(
            member_id="EMP001",
            policy_id="PLUM_GHI_2024",
            claim_category=ClaimCategory.CONSULTATION,
            treatment_date=date(2024, 11, 1),
            claimed_amount=Decimal("1500"),
            documents=[DocumentMetadata(file_id="F001", file_name="rx.jpg")],
        )
        return Claim(submission=submission)

    def test_new_fields_default_to_none(self):
        claim = self.make_claim()
        assert claim.trace_id is None
        assert claim.stopped_at is None
        assert claim.validation_result is None
        assert claim.document_verification_result is None
        assert claim.cross_document_validation_result is None

    def test_blocked_status_is_a_valid_claim_status(self):
        claim = self.make_claim()
        claim.status = ClaimStatus.BLOCKED
        assert claim.status == ClaimStatus.BLOCKED

    def test_can_attach_validation_result(self):
        claim = self.make_claim()
        claim.validation_result = ValidationResult(valid=False, errors=[ValidationIssue(code="X", message="m")])
        assert claim.validation_result.valid is False
