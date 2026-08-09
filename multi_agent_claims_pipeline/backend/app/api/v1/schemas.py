"""
API-layer response schemas for claims.

Kept separate from domain models so the API contract can evolve
independently of internal representation — callers never see raw
SQLAlchemy rows (that's what ClaimRepository._to_domain prevents) and
never see more of the internal Claim than intended.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel

from app.domain.extraction import ClaimExtractionResult, DocumentExtractionResult
from app.domain.models import (
    Claim,
    ClaimCategory,
    ClaimStatus,
    DocumentProcessingStatus,
    DocumentQuality,
    DocumentType,
)
from app.domain.verification import (
    CrossDocumentValidationResult,
    DocumentVerificationResult,
    ValidationResult,
)


class ClaimDocumentSummary(BaseModel):
    """
    Note: deliberately excludes `storage_reference` — that's an internal
    DocumentStorage detail, never exposed through the API (no filesystem
    paths leaked to clients).
    """

    file_id: str
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    document_type: Optional[DocumentType] = None
    quality: Optional[DocumentQuality] = None
    patient_name: Optional[str] = None
    confidence: Optional[float] = None
    processing_status: DocumentProcessingStatus = DocumentProcessingStatus.PENDING
    # Phase 2B: the full typed extraction envelope for this document, if
    # DocumentExtractionAgent extracted it. None means "not extracted" —
    # either extraction hasn't run yet, this document type has no
    # extraction schema, or it failed (see ClaimResponse.extraction_result
    # .skipped / .failures for which one).
    extraction: Optional[DocumentExtractionResult] = None


class ClaimResponse(BaseModel):
    claim_id: str
    member_id: str
    policy_id: str
    claim_category: ClaimCategory
    treatment_date: date
    claimed_amount: Decimal
    status: ClaimStatus
    trace_id: Optional[str] = None
    stopped_at: Optional[str] = None
    user_message: Optional[str] = None
    processing_time_ms: Optional[float] = None
    documents: List[ClaimDocumentSummary]
    validation_result: Optional[ValidationResult] = None
    document_verification_result: Optional[DocumentVerificationResult] = None
    cross_document_validation_result: Optional[CrossDocumentValidationResult] = None
    extraction_result: Optional[ClaimExtractionResult] = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_claim(cls, claim: Claim) -> "ClaimResponse":
        extractions_by_file_id = (
            {e.file_id: e for e in claim.extraction_result.extractions}
            if claim.extraction_result
            else {}
        )
        return cls(
            claim_id=claim.claim_id,
            member_id=claim.submission.member_id,
            policy_id=claim.submission.policy_id,
            claim_category=claim.submission.claim_category,
            treatment_date=claim.submission.treatment_date,
            claimed_amount=claim.submission.claimed_amount,
            status=claim.status,
            trace_id=claim.trace_id,
            stopped_at=claim.stopped_at,
            user_message=claim.user_message,
            processing_time_ms=claim.processing_time_ms,
            documents=[
                ClaimDocumentSummary(
                    file_id=d.metadata.file_id,
                    file_name=d.metadata.file_name,
                    mime_type=d.metadata.mime_type,
                    size_bytes=d.metadata.size_bytes,
                    document_type=d.effective_type,
                    quality=d.metadata.quality,
                    patient_name=d.metadata.patient_name,
                    confidence=d.metadata.confidence,
                    processing_status=d.metadata.processing_status,
                    extraction=extractions_by_file_id.get(d.metadata.file_id),
                )
                for d in claim.documents
            ],
            validation_result=claim.validation_result,
            document_verification_result=claim.document_verification_result,
            cross_document_validation_result=claim.cross_document_validation_result,
            extraction_result=claim.extraction_result,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
        )
