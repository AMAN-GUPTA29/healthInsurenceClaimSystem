"""
Domain models — the canonical data structures for the claims system.

Rules:
- Pure Pydantic models. No database imports. No FastAPI imports. No AI SDK imports.
- These models travel through every layer (API → agents → pipeline → domain).
- All IDs are strings to remain agnostic of storage backend.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enumerations ──────────────────────────────────────────────────────────────


class DocumentType(str, Enum):
    """Types of medical documents that can be submitted with a claim."""

    PRESCRIPTION = "PRESCRIPTION"
    HOSPITAL_BILL = "HOSPITAL_BILL"
    LAB_REPORT = "LAB_REPORT"
    DIAGNOSTIC_REPORT = "DIAGNOSTIC_REPORT"
    PHARMACY_BILL = "PHARMACY_BILL"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"
    DENTAL_REPORT = "DENTAL_REPORT"
    PRE_AUTH_LETTER = "PRE_AUTH_LETTER"
    UNKNOWN = "UNKNOWN"


class ClaimCategory(str, Enum):
    """OPD claim categories matching the policy's opd_categories keys."""

    CONSULTATION = "CONSULTATION"
    DIAGNOSTIC = "DIAGNOSTIC"
    PHARMACY = "PHARMACY"
    DENTAL = "DENTAL"
    VISION = "VISION"
    ALTERNATIVE_MEDICINE = "ALTERNATIVE_MEDICINE"


class DecisionType(str, Enum):
    """Final claim decision outcomes."""

    APPROVED = "APPROVED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    PENDING = "PENDING"  # In-progress state, not a final decision


class ClaimStatus(str, Enum):
    """Processing lifecycle status of a claim."""

    SUBMITTED = "SUBMITTED"
    VALIDATING = "VALIDATING"
    BLOCKED = "BLOCKED"  # Stopped before a decision; member action needed (not just re-upload)
    DOCUMENTS_PENDING = "DOCUMENTS_PENDING"  # Waiting for member to resubmit a specific document
    PROCESSING = "PROCESSING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    DECIDED = "DECIDED"
    CLOSED = "CLOSED"


class RelationshipType(str, Enum):
    """Insured member relationships covered under a family floater plan."""

    SELF = "SELF"
    SPOUSE = "SPOUSE"
    CHILD = "CHILD"
    PARENT = "PARENT"


class DocumentQuality(str, Enum):
    """Quality assessment of an uploaded document."""

    GOOD = "GOOD"
    LOW_QUALITY = "LOW_QUALITY"  # Readable but imperfect (shadows, slight blur)
    PARTIAL = "PARTIAL"  # Some fields readable, others cut off/obscured
    UNREADABLE = "UNREADABLE"  # Cannot be parsed at all
    UNKNOWN = "UNKNOWN"


class DocumentProcessingStatus(str, Enum):
    """Lifecycle status of a single uploaded document within a claim."""

    PENDING = "PENDING"  # Uploaded/stored, not yet classified
    PROCESSED = "PROCESSED"  # AI classification completed successfully
    FAILED = "FAILED"  # AI classification attempted and failed


class RejectionReason(str, Enum):
    """Structured rejection reason codes for use in decisions and explanations."""

    WAITING_PERIOD = "WAITING_PERIOD"
    PRE_AUTH_MISSING = "PRE_AUTH_MISSING"
    PER_CLAIM_EXCEEDED = "PER_CLAIM_EXCEEDED"
    ANNUAL_LIMIT_EXCEEDED = "ANNUAL_LIMIT_EXCEEDED"
    SUB_LIMIT_EXCEEDED = "SUB_LIMIT_EXCEEDED"
    EXCLUDED_CONDITION = "EXCLUDED_CONDITION"
    EXCLUDED_PROCEDURE = "EXCLUDED_PROCEDURE"
    MISSING_DOCUMENTS = "MISSING_DOCUMENTS"
    DOCUMENT_MISMATCH = "DOCUMENT_MISMATCH"
    PATIENT_NOT_MEMBER = "PATIENT_NOT_MEMBER"
    LATE_SUBMISSION = "LATE_SUBMISSION"
    BELOW_MINIMUM_AMOUNT = "BELOW_MINIMUM_AMOUNT"
    MEMBER_INACTIVE = "MEMBER_INACTIVE"
    FRAUD_SUSPECTED = "FRAUD_SUSPECTED"


# ── Member ────────────────────────────────────────────────────────────────────


class Member(BaseModel):
    """
    Represents a policy member (employee or dependent).
    Populated from policy_terms.json — no DB dependency.
    """

    member_id: str
    name: str
    date_of_birth: date
    gender: str
    relationship: RelationshipType
    join_date: date
    primary_member_id: Optional[str] = None  # Set for dependents
    dependents: List[str] = Field(default_factory=list)

    @property
    def age(self) -> int:
        today = date.today()
        born = self.date_of_birth
        return today.year - born.year - ((today.month, today.day) < (born.month, born.day))

    @property
    def is_primary(self) -> bool:
        return self.primary_member_id is None


# ── Document ──────────────────────────────────────────────────────────────────


class DocumentMetadata(BaseModel):
    """
    Metadata about an uploaded document file.
    Populated at submission time; content extraction happens later in the pipeline.

    `declared_type` no longer comes from a UI dropdown (Phase 2A correction —
    the member never selects a type; the AI-detected `detected_type` is the
    only source of truth for verification). It's kept for cases where a
    caller genuinely knows the type in advance (e.g. a future structured
    import), but DocumentVerificationAgent never trusts it over an AI result.
    """

    file_id: str
    file_name: str
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    declared_type: Optional[DocumentType] = None  # What the member says it is (rarely set)
    detected_type: Optional[DocumentType] = None   # What the AI determined — source of truth
    quality: DocumentQuality = DocumentQuality.UNKNOWN
    content_url: Optional[str] = None              # Presigned/public URL (future object storage)
    storage_reference: Optional[str] = None         # DocumentStorage-internal reference — never exposed via the API
    patient_name: Optional[str] = None              # AI-extracted, once classified
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    processing_status: DocumentProcessingStatus = DocumentProcessingStatus.PENDING
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)


class ExtractedDocumentData(BaseModel):
    """
    Structured data extracted from a document by the extraction agent.
    Fields are optional because extraction may be partial on degraded docs.
    """

    document_type: DocumentType
    patient_name: Optional[str] = None
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    date_on_document: Optional[date] = None

    # Prescription-specific
    doctor_name: Optional[str] = None
    doctor_registration: Optional[str] = None
    doctor_specialization: Optional[str] = None
    clinic_name: Optional[str] = None
    diagnosis: Optional[str] = None
    medicines: List[Dict[str, Any]] = Field(default_factory=list)
    tests_ordered: List[str] = Field(default_factory=list)

    # Bill-specific
    hospital_name: Optional[str] = None
    bill_number: Optional[str] = None
    line_items: List[Dict[str, Any]] = Field(default_factory=list)
    subtotal: Optional[Decimal] = None
    gst_amount: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None

    # Lab report-specific
    lab_name: Optional[str] = None
    test_results: List[Dict[str, Any]] = Field(default_factory=list)

    # Quality flags
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    flags: List[str] = Field(default_factory=list)  # e.g. ["RUBBER_STAMP_OVER_TEXT"]
    unextracted_fields: List[str] = Field(default_factory=list)
    raw_text: Optional[str] = None


class Document(BaseModel):
    """Full document record combining metadata and extracted content."""

    metadata: DocumentMetadata
    extracted_data: Optional[ExtractedDocumentData] = None

    @property
    def file_id(self) -> str:
        return self.metadata.file_id

    @property
    def effective_type(self) -> Optional[DocumentType]:
        """Prefer detected type over declared type."""
        return self.metadata.detected_type or self.metadata.declared_type


# ── Claim ─────────────────────────────────────────────────────────────────────


class ClaimHistoryItem(BaseModel):
    """A summary of a previously submitted claim, used in fraud analysis."""

    claim_id: str
    date: date
    amount: Decimal
    provider: Optional[str] = None
    category: Optional[ClaimCategory] = None
    decision: Optional[DecisionType] = None


class ClaimSubmission(BaseModel):
    """
    The raw input payload submitted by a member/API consumer.
    This is the request object — it gets enriched during pipeline processing.
    """

    member_id: str
    policy_id: str
    claim_category: ClaimCategory
    treatment_date: date
    claimed_amount: Decimal = Field(gt=0)
    hospital_name: Optional[str] = None
    documents: List[DocumentMetadata]
    ytd_claims_amount: Decimal = Field(default=Decimal("0"))
    claims_history: List[ClaimHistoryItem] = Field(default_factory=list)
    simulate_component_failure: bool = Field(
        default=False,
        description="Test flag — when True, forces a simulated component failure in Phase 1+.",
    )

    @field_validator("treatment_date")
    @classmethod
    def treatment_date_not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("treatment_date cannot be in the future.")
        return v

    @field_validator("claimed_amount")
    @classmethod
    def claimed_amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("claimed_amount must be positive.")
        return v


class LineItemDecision(BaseModel):
    """Decision at the individual bill line-item level."""

    description: str
    claimed_amount: Decimal
    approved_amount: Decimal
    approved: bool
    rejection_reason: Optional[RejectionReason] = None
    notes: Optional[str] = None


class FinancialBreakdown(BaseModel):
    """Detailed financial calculation supporting the claim decision."""

    claimed_amount: Decimal
    network_discount_percent: float = 0.0
    network_discount_amount: Decimal = Decimal("0")
    amount_after_network_discount: Decimal
    copay_percent: float = 0.0
    copay_amount: Decimal = Decimal("0")
    sub_limit: Optional[Decimal] = None
    sub_limit_applied: bool = False
    per_claim_limit: Optional[Decimal] = None
    per_claim_limit_applied: bool = False
    approved_amount: Decimal
    calculation_steps: List[str] = Field(
        default_factory=list,
        description="Human-readable ordered steps describing the calculation.",
    )


class ComponentTrace(BaseModel):
    """Record of what happened in a single pipeline component."""

    component_name: str
    status: str  # "completed" | "failed" | "skipped"
    duration_ms: Optional[float] = None
    error: Optional[str] = None
    notes: Optional[str] = None


class ClaimDecision(BaseModel):
    """
    The final output of the claims pipeline for a single claim.
    This is what the API returns and what gets persisted.
    """

    claim_id: str
    member_id: str
    policy_id: str
    category: ClaimCategory
    treatment_date: date
    claimed_amount: Decimal

    # Decision
    decision: DecisionType
    approved_amount: Optional[Decimal] = None
    rejection_reasons: List[RejectionReason] = Field(default_factory=list)
    line_item_decisions: List[LineItemDecision] = Field(default_factory=list)

    # Financial
    financial_breakdown: Optional[FinancialBreakdown] = None

    # Quality / Confidence
    confidence_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="0.0 = no confidence, 1.0 = maximum confidence. Reduced on component failures.",
    )

    # Explanation
    explanation: Optional[str] = None
    member_facing_message: Optional[str] = None

    # Observability
    component_traces: List[ComponentTrace] = Field(default_factory=list)
    has_component_failures: bool = False
    manual_review_recommended: bool = False
    fraud_signals: List[str] = Field(default_factory=list)

    # Metadata
    decided_at: datetime = Field(default_factory=datetime.utcnow)
    processing_time_ms: Optional[float] = None


# app.domain.verification imports DocumentType/DocumentQuality from this module.
# Importing it here (mid-file, after those enums are already defined, rather
# than at the top of the file) lets Claim reference its result types directly
# without a forward-ref/model_rebuild dance — Python resolves this safely
# because app.domain.models is already partially initialised (with the names
# verification.py needs) by the time this import runs.
from app.domain.verification import (  # noqa: E402
    CrossDocumentValidationResult,
    DocumentVerificationResult,
    ValidationResult,
)

# Same late-import pattern as above, for the same reason: app.domain.extraction
# imports DocumentType/DocumentQuality from this module, so Claim can only
# reference ClaimExtractionResult after those names already exist.
from app.domain.extraction import ClaimExtractionResult  # noqa: E402


def generate_claim_id() -> str:
    """
    Shared claim_id generator — used both as Claim's own default_factory
    and by callers (e.g. the claims API) that need the ID *before*
    constructing a Claim, such as to name a document's storage path.
    """
    return f"CLM-{uuid4().hex[:8].upper()}"


class Claim(BaseModel):
    """
    Full claim record, enriched through pipeline processing.
    Combines the original submission with processed data.

    Phase 2A fields (trace_id, *_result, user_message, stopped_at) are
    populated by ClaimsPipeline as the claim moves through claim
    validation, document verification, and cross-document validation.
    They stay None until that stage has actually run — a None
    `document_verification_result` means "not reached yet", not "passed".

    Phase 2B adds `extraction_result`, populated the same way once
    DocumentExtractionAgent runs (after cross-document validation passes).
    A None `extraction_result` means either "not reached yet" or "no
    extraction agent configured" (see ClaimsPipeline) — not "failed"; a
    per-document extraction failure is recorded inside
    `extraction_result.failures`, not by leaving the whole field None.
    """

    claim_id: str = Field(default_factory=lambda: generate_claim_id())
    submission: ClaimSubmission
    status: ClaimStatus = ClaimStatus.SUBMITTED
    documents: List[Document] = Field(default_factory=list)
    member: Optional[Member] = None
    decision: Optional[ClaimDecision] = None

    # ── Phase 2A: early pipeline stages ─────────────────────────────────────
    trace_id: Optional[str] = None
    stopped_at: Optional[str] = None  # TraceComponent value where processing stopped, if any
    user_message: Optional[str] = None
    validation_result: Optional[ValidationResult] = None
    document_verification_result: Optional[DocumentVerificationResult] = None
    cross_document_validation_result: Optional[CrossDocumentValidationResult] = None
    extraction_result: Optional[ClaimExtractionResult] = None
    processing_time_ms: Optional[float] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def sync_documents_from_submission(self) -> "Claim":
        """Initialise Document wrappers from submission metadata if not already set."""
        if not self.documents and self.submission.documents:
            self.documents = [
                Document(metadata=meta) for meta in self.submission.documents
            ]
        return self
