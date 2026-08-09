"""
DocumentInputAdapter — the single input boundary for claim documents.

Two entry points converge on the same internal representation
(ClaimSubmission, plus a classifications map for any pre-known ground
truth), which is all ClaimsPipeline / DocumentVerificationAgent ever see:

1. `to_domain(ClaimSubmissionRequest)` — the evaluation path. Used by
   app/evaluation/runner.py to convert test_cases.json fixtures, which
   carry ground-truth fields (actual_type/quality/patient_name_on_doc)
   standing in for what a real AI classification would have produced.
   DocumentVerificationAgent uses a pre-supplied classification when one
   exists in the returned map, skipping the AI call for that document.

2. `from_uploads(...)` — the real production path (Phase 2A correction).
   Takes actual uploaded file bytes, validates and persists each one via
   DocumentStorage, and returns a ClaimSubmission with NO pre-supplied
   classifications at all — every real document is always classified by
   the real AIProvider from its actual content. There is no ground truth
   to fabricate for a real upload; the adapter's job here is purely
   validation + storage + shape conversion.

This adapter contains no business rules of its own (no "is this claim
valid" logic) and no knowledge of specific test cases — kept outside
every business agent as required.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Protocol, Tuple

from pydantic import BaseModel, Field

from app.domain.errors import EmptyDocumentError
from app.domain.models import (
    ClaimCategory,
    ClaimHistoryItem,
    ClaimSubmission,
    DocumentMetadata,
    DocumentQuality,
    DocumentType,
)
from app.domain.verification import DocumentClassification
from app.storage.document_storage import DocumentStorage, validate_upload


class ClaimDocumentInput(BaseModel):
    """
    One document as submitted through the API.

    A real submission only ever sets file_id/file_name/declared_type. The
    remaining fields (actual_type/quality/patient_name_on_doc) exist so the
    evaluation layer can pass the assignment's fixture ground truth through
    this exact same request shape — see module docstring.
    """

    file_id: str
    file_name: Optional[str] = None
    declared_type: Optional[DocumentType] = None

    # Evaluation-fixture-only fields (ground truth; absent for real submissions)
    actual_type: Optional[DocumentType] = None
    quality: Optional[DocumentQuality] = None
    patient_name_on_doc: Optional[str] = None


class ClaimSubmissionRequest(BaseModel):
    """The POST /api/v1/claims request body."""

    member_id: str
    policy_id: str
    claim_category: ClaimCategory
    treatment_date: date
    claimed_amount: Decimal = Field(gt=0)
    hospital_name: Optional[str] = None
    ytd_claims_amount: Decimal = Decimal("0")
    documents: List[ClaimDocumentInput]
    claims_history: List[ClaimHistoryItem] = Field(default_factory=list)
    simulate_component_failure: bool = False


class UploadLike(Protocol):
    """
    The minimal shape this adapter needs from an uploaded file — matches
    FastAPI's UploadFile exactly, without importing FastAPI into this
    module (keeps the adapter testable/usable outside the web framework).
    """

    filename: Optional[str]
    content_type: Optional[str]

    async def read(self) -> bytes: ...


class DocumentInputAdapter:
    """Converts a claim submission (fixture-shaped or real uploads) into the
    pipeline's internal input shape."""

    async def from_uploads(
        self,
        *,
        member_id: str,
        policy_id: str,
        claim_category: ClaimCategory,
        treatment_date: date,
        claimed_amount: Decimal,
        hospital_name: Optional[str],
        ytd_claims_amount: Decimal,
        uploads: List[UploadLike],
        claim_id: str,
        storage: DocumentStorage,
        simulate_component_failure: bool = False,
    ) -> ClaimSubmission:
        """
        Real production path: validate + persist each uploaded file, build
        a ClaimSubmission with no pre-supplied classifications — every
        document here gets classified from its actual content by the real
        AIProvider (see DocumentVerificationAgent). No document type is
        ever assumed from a filename or a UI selection.
        """
        if not uploads:
            raise EmptyDocumentError()

        documents: List[DocumentMetadata] = []
        for upload in uploads:
            content = await upload.read()
            filename = upload.filename or "document"
            media_type = validate_upload(
                filename=filename, content_type=upload.content_type, content=content
            )
            storage_reference = await storage.save(claim_id=claim_id, filename=filename, content=content)
            generated_name = storage_reference.rsplit("/", 1)[-1]
            file_id = generated_name.rsplit(".", 1)[0]  # strip extension, keep the generated UUID
            documents.append(
                DocumentMetadata(
                    file_id=file_id,
                    file_name=filename,
                    mime_type=media_type.value,
                    size_bytes=len(content),
                    storage_reference=storage_reference,
                )
            )

        return ClaimSubmission(
            member_id=member_id,
            policy_id=policy_id,
            claim_category=claim_category,
            treatment_date=treatment_date,
            claimed_amount=claimed_amount,
            hospital_name=hospital_name,
            documents=documents,
            ytd_claims_amount=ytd_claims_amount,
            simulate_component_failure=simulate_component_failure,
        )

    def to_domain(
        self, request: ClaimSubmissionRequest
    ) -> Tuple[ClaimSubmission, Dict[str, DocumentClassification]]:
        documents: List[DocumentMetadata] = []
        classifications: Dict[str, DocumentClassification] = {}

        for doc_input in request.documents:
            file_name = doc_input.file_name or doc_input.file_id
            documents.append(
                DocumentMetadata(
                    file_id=doc_input.file_id,
                    file_name=file_name,
                    declared_type=doc_input.declared_type,
                )
            )

            if doc_input.actual_type is not None:
                classifications[doc_input.file_id] = DocumentClassification(
                    file_id=doc_input.file_id,
                    document_type=doc_input.actual_type,
                    quality=doc_input.quality or DocumentQuality.GOOD,
                    patient_name=doc_input.patient_name_on_doc,
                    confidence=1.0,
                    source="fixture",
                )

        submission = ClaimSubmission(
            member_id=request.member_id,
            policy_id=request.policy_id,
            claim_category=request.claim_category,
            treatment_date=request.treatment_date,
            claimed_amount=request.claimed_amount,
            hospital_name=request.hospital_name,
            documents=documents,
            ytd_claims_amount=request.ytd_claims_amount,
            claims_history=request.claims_history,
            simulate_component_failure=request.simulate_component_failure,
        )

        return submission, classifications
