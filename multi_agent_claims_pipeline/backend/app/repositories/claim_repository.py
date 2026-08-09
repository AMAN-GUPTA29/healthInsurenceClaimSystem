"""
ClaimRepository — persistence for claims and their documents.

Unlike TraceRepository, this genuinely is single-entity CRUD-by-id, so it
implements BaseRepository[Claim, str] properly (get_by_id/save).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select

from app.domain.extraction import ClaimExtractionResult, DocumentExtractionFailure, DocumentExtractionResult
from app.domain.models import (
    Claim,
    ClaimCategory,
    ClaimStatus,
    ClaimSubmission,
    Document,
    DocumentMetadata,
    DocumentProcessingStatus,
    DocumentQuality,
    DocumentType,
)
from app.domain.trace import AITraceMetadata
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
            row.extraction_summary_json = (
                _extraction_summary_json(claim.extraction_result) if claim.extraction_result else None
            )
            row.created_at = claim.created_at
            row.updated_at = claim.updated_at

            extractions_by_file_id = (
                {e.file_id: e for e in claim.extraction_result.extractions}
                if claim.extraction_result
                else {}
            )
            failures_by_file_id = (
                {f.file_id: f for f in claim.extraction_result.failures} if claim.extraction_result else {}
            )
            skipped_file_ids = set(claim.extraction_result.skipped) if claim.extraction_result else set()

            for doc in claim.documents:
                extraction_status = "NOT_ATTEMPTED"
                diagnosis = treatment = doctor_name = None
                document_date = None
                total_amount = None
                extraction_json = None

                envelope = extractions_by_file_id.get(doc.metadata.file_id)
                if envelope is not None:
                    extraction_status = "EXTRACTED"
                    projection = _extraction_projection(envelope)
                    diagnosis = projection["diagnosis"]
                    treatment = projection["treatment"]
                    document_date = projection["document_date"]
                    doctor_name = projection["doctor_name"]
                    total_amount = projection["total_amount"]
                    extraction_json = envelope.model_dump(mode="json")
                elif doc.metadata.file_id in failures_by_file_id:
                    extraction_status = "FAILED"
                elif doc.metadata.file_id in skipped_file_ids:
                    extraction_status = "SKIPPED"

                session.add(
                    ClaimDocumentORM(
                        claim_id=claim.claim_id,
                        file_id=doc.metadata.file_id,
                        file_name=doc.metadata.file_name,
                        mime_type=doc.metadata.mime_type,
                        size_bytes=doc.metadata.size_bytes,
                        storage_reference=doc.metadata.storage_reference,
                        declared_type=doc.metadata.declared_type.value if doc.metadata.declared_type else None,
                        detected_type=doc.metadata.detected_type.value if doc.metadata.detected_type else None,
                        quality=doc.metadata.quality.value,
                        patient_name=doc.metadata.patient_name,
                        confidence=doc.metadata.confidence,
                        processing_status=doc.metadata.processing_status.value,
                        extraction_status=extraction_status,
                        diagnosis=diagnosis,
                        treatment=treatment,
                        document_date=document_date,
                        doctor_name=doctor_name,
                        total_amount=total_amount,
                        extraction_json=extraction_json,
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
                mime_type=d.mime_type,
                size_bytes=d.size_bytes,
                storage_reference=d.storage_reference,
                declared_type=DocumentType(d.declared_type) if d.declared_type else None,
                detected_type=DocumentType(d.detected_type) if d.detected_type else None,
                quality=DocumentQuality(d.quality),
                patient_name=d.patient_name,
                confidence=d.confidence,
                processing_status=DocumentProcessingStatus(d.processing_status),
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
        extraction_result=_extraction_result_from_rows(row, doc_rows),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ── Phase 2B: extraction persistence helpers ────────────────────────────────


def _extraction_projection(envelope: DocumentExtractionResult) -> Dict[str, Any]:
    """
    Derive the denormalised, queryable columns from the full typed
    envelope. Uses getattr because the six extraction schemas
    (PrescriptionExtraction, HospitalBillExtraction, ...) don't share a
    common field name for "treatment"/"the doctor"/"the headline amount" —
    see app/domain/extraction.py for the full per-type field lists.
    """
    extraction = envelope.extraction
    doctor = getattr(extraction, "doctor", None) or getattr(extraction, "dentist", None)
    return {
        "diagnosis": getattr(extraction, "diagnosis", None),
        "treatment": (
            getattr(extraction, "treatment", None)
            or getattr(extraction, "procedure", None)
            or getattr(extraction, "procedure_or_treatment", None)
        ),
        "document_date": envelope.document_date,
        "doctor_name": doctor.name if doctor is not None else None,
        "total_amount": (
            getattr(extraction, "total", None)
            or getattr(extraction, "total_amount", None)
            or getattr(extraction, "amount", None)
        ),
    }


def _extraction_summary_json(result: ClaimExtractionResult) -> Dict[str, Any]:
    """Claim-level rollup persisted on ClaimORM — see its docstring for why
    this is split from the per-document payload on ClaimDocumentORM."""
    return {
        "failures": [f.model_dump(mode="json") for f in result.failures],
        "skipped": list(result.skipped),
        "ai_calls": [c.model_dump(mode="json") for c in result.ai_calls],
        "confidence": result.confidence,
        "has_failures": result.has_failures,
    }


def _extraction_result_from_rows(
    row: ClaimORM, doc_rows: List[ClaimDocumentORM]
) -> Optional[ClaimExtractionResult]:
    """Rehydrate ClaimExtractionResult from ClaimORM.extraction_summary_json
    (claim-level rollup) + each ClaimDocumentORM.extraction_json (the full
    per-document envelope, including the discriminated `extraction` union —
    DocumentExtractionResult.model_validate resolves it via the
    `document_type` field stored inside that JSON)."""
    if row.extraction_summary_json is None:
        return None

    extractions = [
        DocumentExtractionResult.model_validate(d.extraction_json)
        for d in doc_rows
        if d.extraction_json is not None
    ]
    summary = row.extraction_summary_json
    return ClaimExtractionResult(
        extractions=extractions,
        failures=[DocumentExtractionFailure(**f) for f in summary.get("failures", [])],
        skipped=summary.get("skipped", []),
        confidence=summary.get("confidence"),
        has_failures=summary.get("has_failures", False),
        ai_calls=[AITraceMetadata(**c) for c in summary.get("ai_calls", [])],
    )
