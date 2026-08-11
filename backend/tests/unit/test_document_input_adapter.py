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

import pytest

from app.domain.errors import EmptyDocumentError, UnsupportedDocumentTypeError
from app.domain.models import ClaimCategory, DocumentProcessingStatus, DocumentQuality, DocumentType
from app.services.document_input_adapter import (
    ClaimDocumentInput,
    ClaimSubmissionRequest,
    DocumentInputAdapter,
)
from app.storage.document_storage import LocalFileDocumentStorage


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


class _FakeUpload:
    def __init__(self, filename, content_type, content):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self) -> bytes:
        return self._content


JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 50
PDF_BYTES = b"%PDF-1.4\n" + b"x" * 50


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def storage(tmp_path):
    return LocalFileDocumentStorage(base_dir=str(tmp_path))


class TestFromUploads:
    """The real production path (Phase 2A correction): actual file bytes,
    never a UI-declared type or ground-truth fixture field."""

    @pytest.mark.anyio
    async def test_produces_no_pre_supplied_classifications(self, storage):
        submission = await DocumentInputAdapter().from_uploads(
            member_id="EMP001",
            policy_id="PLUM_GHI_2024",
            claim_category=ClaimCategory.CONSULTATION,
            treatment_date=date(2024, 11, 1),
            claimed_amount=Decimal("1500"),
            hospital_name=None,
            ytd_claims_amount=Decimal("0"),
            uploads=[_FakeUpload("rx.jpg", "image/jpeg", JPEG_BYTES)],
            claim_id="CLM-TEST",
            storage=storage,
        )
        assert submission.documents[0].declared_type is None
        assert submission.documents[0].detected_type is None  # not yet classified
        assert submission.documents[0].processing_status == DocumentProcessingStatus.PENDING

    @pytest.mark.anyio
    async def test_multiple_uploads_each_get_a_distinct_file_id(self, storage):
        submission = await DocumentInputAdapter().from_uploads(
            member_id="EMP001",
            policy_id="PLUM_GHI_2024",
            claim_category=ClaimCategory.CONSULTATION,
            treatment_date=date(2024, 11, 1),
            claimed_amount=Decimal("1500"),
            hospital_name=None,
            ytd_claims_amount=Decimal("0"),
            uploads=[
                _FakeUpload("rx.jpg", "image/jpeg", JPEG_BYTES),
                _FakeUpload("bill.pdf", "application/pdf", PDF_BYTES),
            ],
            claim_id="CLM-TEST",
            storage=storage,
        )
        assert len(submission.documents) == 2
        ids = {d.file_id for d in submission.documents}
        assert len(ids) == 2

    @pytest.mark.anyio
    async def test_each_document_gets_a_storage_reference(self, storage):
        submission = await DocumentInputAdapter().from_uploads(
            member_id="EMP001",
            policy_id="PLUM_GHI_2024",
            claim_category=ClaimCategory.CONSULTATION,
            treatment_date=date(2024, 11, 1),
            claimed_amount=Decimal("1500"),
            hospital_name=None,
            ytd_claims_amount=Decimal("0"),
            uploads=[_FakeUpload("rx.jpg", "image/jpeg", JPEG_BYTES)],
            claim_id="CLM-TEST",
            storage=storage,
        )
        doc = submission.documents[0]
        assert doc.storage_reference is not None
        stored_bytes = await storage.read(doc.storage_reference)
        assert stored_bytes == JPEG_BYTES

    @pytest.mark.anyio
    async def test_empty_upload_list_raises(self, storage):
        with pytest.raises(EmptyDocumentError):
            await DocumentInputAdapter().from_uploads(
                member_id="EMP001",
                policy_id="PLUM_GHI_2024",
                claim_category=ClaimCategory.CONSULTATION,
                treatment_date=date(2024, 11, 1),
                claimed_amount=Decimal("1500"),
                hospital_name=None,
                ytd_claims_amount=Decimal("0"),
                uploads=[],
                claim_id="CLM-TEST",
                storage=storage,
            )

    @pytest.mark.anyio
    async def test_unsupported_file_type_raises(self, storage):
        with pytest.raises(UnsupportedDocumentTypeError):
            await DocumentInputAdapter().from_uploads(
                member_id="EMP001",
                policy_id="PLUM_GHI_2024",
                claim_category=ClaimCategory.CONSULTATION,
                treatment_date=date(2024, 11, 1),
                claimed_amount=Decimal("1500"),
                hospital_name=None,
                ytd_claims_amount=Decimal("0"),
                uploads=[_FakeUpload("virus.exe", "application/x-msdownload", b"MZ...")],
                claim_id="CLM-TEST",
                storage=storage,
            )

    @pytest.mark.anyio
    async def test_one_bad_file_among_several_still_raises(self, storage):
        """A mixed batch with one invalid file must fail the whole
        submission, not silently drop the bad one."""
        with pytest.raises(UnsupportedDocumentTypeError):
            await DocumentInputAdapter().from_uploads(
                member_id="EMP001",
                policy_id="PLUM_GHI_2024",
                claim_category=ClaimCategory.CONSULTATION,
                treatment_date=date(2024, 11, 1),
                claimed_amount=Decimal("1500"),
                hospital_name=None,
                ytd_claims_amount=Decimal("0"),
                uploads=[
                    _FakeUpload("rx.jpg", "image/jpeg", JPEG_BYTES),
                    _FakeUpload("bad.txt", "text/plain", b"not a document"),
                ],
                claim_id="CLM-TEST",
                storage=storage,
            )
