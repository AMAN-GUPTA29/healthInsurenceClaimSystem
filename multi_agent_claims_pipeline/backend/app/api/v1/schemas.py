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

from app.domain.models import Claim, ClaimCategory, ClaimStatus, DocumentQuality, DocumentType
from app.domain.verification import (
    CrossDocumentValidationResult,
    DocumentVerificationResult,
    ValidationResult,
)


class ClaimDocumentSummary(BaseModel):
    file_id: str
    file_name: Optional[str] = None
    document_type: Optional[DocumentType] = None
    quality: Optional[DocumentQuality] = None


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
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_claim(cls, claim: Claim) -> "ClaimResponse":
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
                    document_type=d.effective_type,
                    quality=d.metadata.quality,
                )
                for d in claim.documents
            ],
            validation_result=claim.validation_result,
            document_verification_result=claim.document_verification_result,
            cross_document_validation_result=claim.cross_document_validation_result,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
        )
