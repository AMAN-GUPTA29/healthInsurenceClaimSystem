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
from app.domain.fraud import FraudAnalysisResult
from app.domain.models import (
    Claim,
    ClaimCategory,
    ClaimDecision,
    ClaimStatus,
    ClaimSummary,
    DocumentProcessingStatus,
    DocumentQuality,
    DocumentType,
    FinancialBreakdown,
)
from app.domain.policy_evaluation import PolicyEvaluationResult
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
    # Phase 2C — directly reuse the domain result models (same precedent as
    # every *_result field above): None means "not reached", "not
    # configured for this pipeline", or "this stage failed and degraded
    # gracefully" — check the trace for which.
    policy_evaluation_result: Optional[PolicyEvaluationResult] = None
    financial_calculation_result: Optional[FinancialBreakdown] = None
    fraud_analysis_result: Optional[FraudAnalysisResult] = None
    # Phase 2D — same precedent as every *_result field above: reuse the
    # domain model wholesale rather than flattening its fields onto
    # ClaimResponse. `decision.decision`/`.approved_amount`/
    # `.confidence_score`/`.reason_code`/`.explanation` (operations-facing)/
    # `.member_facing_message` (member-facing)/`.explanation_detail` (the
    # full structured ExplanationResult) together satisfy every field
    # assignment.md point 4 requires — see COMPONENT_CONTRACTS.docx
    # "16. API Contracts". None means "not reached" (claim
    # stopped early — see `status`/`stopped_at`) or "decision generation
    # not configured for this pipeline" — never a fabricated decision.
    decision: Optional[ClaimDecision] = None
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
            policy_evaluation_result=claim.policy_evaluation_result,
            financial_calculation_result=claim.financial_calculation_result,
            fraud_analysis_result=claim.fraud_analysis_result,
            decision=claim.decision,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
        )


class ClaimListResponse(BaseModel):
    """Response for `GET /api/v1/claims` (Phase 4) — the Claim History
    list. `ClaimSummary` (app/domain/models.py) is reused wholesale, same
    "reuse the domain model directly in the API response" precedent as
    `ClaimResponse.decision` above."""

    claims: List[ClaimSummary]
    count: int
