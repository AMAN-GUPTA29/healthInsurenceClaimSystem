"""
Unit tests for DocumentInputAdapter.

Verifies the shared input boundary: a request with fixture ground-truth
fields (actual_type/quality/patient_name_on_doc) produces pre-supplied
classifications; a request without them (the real-submission shape)
produces none, so DocumentVerificationAgent will fall back to AI.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.models import ClaimCategory, DocumentQuality, DocumentType
from app.services.document_input_adapter import (
    ClaimDocumentInput,
    ClaimSubmissionRequest,
    DocumentInputAdapter,
)


def make_request(**overrides) -> ClaimSubmissionRequest:
    defaults = dict(
        member_id="EMP001",
        policy_id="PLUM_GHI_2024",
        claim_category=ClaimCategory.CONSULTATION,
        treatment_date=date(2024, 11, 1),
        claimed_amount=Decimal("1500"),
        documents=[ClaimDocumentInput(file_id="F001", file_name="rx.jpg")],
    )
    defaults.update(overrides)
    return ClaimSubmissionRequest(**defaults)


class TestRealSubmissionShape:
    def test_no_classification_produced_without_fixture_fields(self):
        request = make_request(
            documents=[ClaimDocumentInput(file_id="F001", file_name="rx.jpg", declared_type=DocumentType.PRESCRIPTION)]
        )
        submission, classifications = DocumentInputAdapter().to_domain(request)
        assert classifications == {}
        assert submission.documents[0].declared_type == DocumentType.PRESCRIPTION

    def test_missing_file_name_defaults_to_file_id(self):
        request = make_request(documents=[ClaimDocumentInput(file_id="F999")])
        submission, _ = DocumentInputAdapter().to_domain(request)
        assert submission.documents[0].file_name == "F999"


class TestFixtureShape:
    def test_actual_type_produces_a_classification(self):
        request = make_request(
            documents=[ClaimDocumentInput(file_id="F001", file_name="rx.jpg", actual_type=DocumentType.PRESCRIPTION)]
        )
        _, classifications = DocumentInputAdapter().to_domain(request)
        assert "F001" in classifications
        assert classifications["F001"].document_type == DocumentType.PRESCRIPTION
        assert classifications["F001"].source == "fixture"

    def test_quality_defaults_to_good_when_not_given(self):
        request = make_request(
            documents=[ClaimDocumentInput(file_id="F001", actual_type=DocumentType.PRESCRIPTION)]
        )
        _, classifications = DocumentInputAdapter().to_domain(request)
        assert classifications["F001"].quality == DocumentQuality.GOOD

    def test_explicit_quality_preserved(self):
        request = make_request(
            documents=[
                ClaimDocumentInput(
                    file_id="F001", actual_type=DocumentType.PHARMACY_BILL, quality=DocumentQuality.UNREADABLE
                )
            ]
        )
        _, classifications = DocumentInputAdapter().to_domain(request)
        assert classifications["F001"].quality == DocumentQuality.UNREADABLE

    def test_patient_name_on_doc_carried_through(self):
        request = make_request(
            documents=[
                ClaimDocumentInput(
                    file_id="F001", actual_type=DocumentType.PRESCRIPTION, patient_name_on_doc="Rajesh Kumar"
                )
            ]
        )
        _, classifications = DocumentInputAdapter().to_domain(request)
        assert classifications["F001"].patient_name == "Rajesh Kumar"

    def test_mixed_fixture_and_real_documents_in_one_request(self):
        """Not every document in a request needs ground truth — the
        adapter handles a mix without special-casing."""
        request = make_request(
            documents=[
                ClaimDocumentInput(file_id="F001", actual_type=DocumentType.PRESCRIPTION),
                ClaimDocumentInput(file_id="F002", declared_type=DocumentType.HOSPITAL_BILL),
            ]
        )
        submission, classifications = DocumentInputAdapter().to_domain(request)
        assert "F001" in classifications
        assert "F002" not in classifications
        assert len(submission.documents) == 2


class TestSubmissionFieldsPassThrough:
    def test_claimed_amount_is_decimal(self):
        request = make_request(claimed_amount=Decimal("1500"))
        submission, _ = DocumentInputAdapter().to_domain(request)
        assert submission.claimed_amount == Decimal("1500")
        assert isinstance(submission.claimed_amount, Decimal)

    def test_simulate_component_failure_flag_passed_through(self):
        request = make_request(simulate_component_failure=True)
        submission, _ = DocumentInputAdapter().to_domain(request)
        assert submission.simulate_component_failure is True
