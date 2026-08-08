"""
ClaimRepository — persistence for claims and their documents.

Unlike TraceRepository, this genuinely is single-entity CRUD-by-id, so it
implements BaseRepository[Claim, str] properly (get_by_id/save).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select

from app.domain.models import (
    Claim,
    ClaimCategory,
    ClaimStatus,
    ClaimSubmission,
    Document,
    DocumentMetadata,
    DocumentQuality,
    DocumentType,
)
from app.domain.verification import (
    CrossDocumentValidationResult,
    DocumentVerificationResult,
    ValidationResult,
)
from app.repositories.base import BaseRepository
from app.repositories.claim_models import ClaimDocumentORM, ClaimORM
from app.repositories.database import get_session


class ClaimRepository(BaseRepository[Claim, str]):
    async def save(self, claim: Claim) -> Claim:
        """Insert or update a claim and its document rows."""
        async with get_session() as session:
            row = await session.get(ClaimORM, claim.claim_id)
            if row is None:
                row = ClaimORM(claim_id=claim.claim_id)
                session.add(row)
            else:
                await session.execute(
                    delete(ClaimDocumentORM).where(ClaimDocumentORM.claim_id == claim.claim_id)
                )

            row.member_id = claim.submission.member_id
            row.policy_id = claim.submission.policy_id
            row.claim_category = claim.submission.claim_category.value
            row.treatment_date = claim.submission.treatment_date
            row.claimed_amount = claim.submission.claimed_amount
            row.hospital_name = claim.submission.hospital_name
            row.ytd_claims_amount = claim.submission.ytd_claims_amount
            row.status = claim.status.value
            row.trace_id = claim.trace_id
            row.stopped_at = claim.stopped_at
            row.user_message = claim.user_message
            row.processing_time_ms = claim.processing_time_ms
            row.validation_result_json = (
                claim.validation_result.model_dump(mode="json") if claim.validation_result else None
            )
            row.document_verification_result_json = (
                claim.document_verification_result.model_dump(mode="json")
                if claim.document_verification_result
                else None
            )
            row.cross_document_validation_result_json = (
                claim.cross_document_validation_result.model_dump(mode="json")
                if claim.cross_document_validation_result
                else None
            )
            row.created_at = claim.created_at
            row.updated_at = claim.updated_at

            for doc in claim.documents:
                session.add(
                    ClaimDocumentORM(
                        claim_id=claim.claim_id,
                        file_id=doc.metadata.file_id,
                        file_name=doc.metadata.file_name,
                        declared_type=doc.metadata.declared_type.value if doc.metadata.declared_type else None,
                        detected_type=doc.metadata.detected_type.value if doc.metadata.detected_type else None,
                        quality=doc.metadata.quality.value,
                    )
                )

        return claim

    async def get_by_id(self, id: str) -> Optional[Claim]:
        async with get_session() as session:
            row = await session.get(ClaimORM, id)
            if row is None:
                return None
            doc_rows = (
                await session.execute(
                    select(ClaimDocumentORM).where(ClaimDocumentORM.claim_id == id).order_by(ClaimDocumentORM.id)
                )
            ).scalars().all()
            return _to_domain(row, doc_rows)


def _to_domain(row: ClaimORM, doc_rows: List[ClaimDocumentORM]) -> Claim:
    documents = [
        Document(
            metadata=DocumentMetadata(
                file_id=d.file_id,
                file_name=d.file_name or d.file_id,
                declared_type=DocumentType(d.declared_type) if d.declared_type else None,
                detected_type=DocumentType(d.detected_type) if d.detected_type else None,
                quality=DocumentQuality(d.quality),
            )
        )
        for d in doc_rows
    ]

    submission = ClaimSubmission(
        member_id=row.member_id,
        policy_id=row.policy_id,
        claim_category=ClaimCategory(row.claim_category),
        treatment_date=row.treatment_date,
        claimed_amount=row.claimed_amount,
        hospital_name=row.hospital_name,
        documents=[d.metadata for d in documents],
        ytd_claims_amount=row.ytd_claims_amount,
    )

    return Claim(
        claim_id=row.claim_id,
        submission=submission,
        status=ClaimStatus(row.status),
        documents=documents,
        trace_id=row.trace_id,
        stopped_at=row.stopped_at,
        user_message=row.user_message,
        processing_time_ms=float(row.processing_time_ms) if row.processing_time_ms is not None else None,
        validation_result=(
            ValidationResult(**row.validation_result_json) if row.validation_result_json else None
        ),
        document_verification_result=(
            DocumentVerificationResult(**row.document_verification_result_json)
            if row.document_verification_result_json
            else None
        ),
        cross_document_validation_result=(
            CrossDocumentValidationResult(**row.cross_document_validation_result_json)
            if row.cross_document_validation_result_json
            else None
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
