"""
SQLAlchemy ORM models for claim persistence.

Same pattern as trace_models.py: this module holds only the storage
mapping. ClaimRepository is the only place that converts between these
rows and the domain models (Claim, DocumentMetadata, ValidationResult,
DocumentVerificationResult, CrossDocumentValidationResult).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.repositories.database import Base


class ClaimORM(Base):
    """One row per claim. Nested results (validation/verification) are
    stored as JSON — same approach as TraceEventORM's metadata_json,
    appropriate here since they're read as a whole with the claim, never
    queried field-by-field."""

    __tablename__ = "claims"

    claim_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(32), index=True)
    policy_id: Mapped[str] = mapped_column(String(64))
    claim_category: Mapped[str] = mapped_column(String(32))
    treatment_date: Mapped[date] = mapped_column(Date)
    claimed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2, asdecimal=True))
    hospital_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ytd_claims_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2, asdecimal=True), default=Decimal("0"))

    status: Mapped[str] = mapped_column(String(32))
    trace_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    stopped_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processing_time_ms: Mapped[Optional[float]] = mapped_column(Numeric(12, 3), nullable=True)

    validation_result_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    document_verification_result_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    cross_document_validation_result_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    # Phase 2B: claim-level rollup only (failures/skipped file_ids, ai_calls,
    # overall confidence) — the heavy per-document payload lives on
    # ClaimDocumentORM.extraction_json instead (see that model's docstring).
    extraction_summary_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ClaimDocumentORM(Base):
    """
    One row per document metadata entry on a claim.

    `storage_reference` is persisted (DocumentVerificationAgent/future
    reprocessing needs it to re-read the file) but deliberately never
    surfaced through the API — see ClaimDocumentSummary in app/api/v1/schemas.py.
    """

    __tablename__ = "claim_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(String(32), ForeignKey("claims.claim_id"), index=True)
    file_id: Mapped[str] = mapped_column(String(64))
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    storage_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    declared_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    detected_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    quality: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    patient_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    processing_status: Mapped[str] = mapped_column(String(32), default="PENDING")

    # ── Phase 2B: document extraction ───────────────────────────────────────
    #
    # Hybrid persistence per docs/component-contracts.md "DocumentExtractionAgent":
    # a handful of genuinely queryable fields as real columns (diagnosis,
    # treatment, document_date, doctor_name, total_amount — the fields most
    # likely to matter to a future SQL query or PolicyEngine lookup) plus
    # `extraction_json`, the full typed DocumentExtractionResult envelope
    # (patient/quality/confidence/warnings/evidence/the type-specific
    # payload), which is what ClaimRepository actually rehydrates from —
    # the queryable columns are a denormalised projection of it, not a
    # second source of truth. Never an opaque blob alone (see instruction).
    extraction_status: Mapped[str] = mapped_column(String(32), default="NOT_ATTEMPTED")
    diagnosis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    treatment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    document_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    doctor_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    total_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2, asdecimal=True), nullable=True)
    extraction_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
