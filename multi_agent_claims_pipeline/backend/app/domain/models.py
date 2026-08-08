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
    DOCUMENTS_PENDING = "DOCUMENTS_PENDING"  # Waiting for member to resubmit docs
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
    DEGRADED = "DEGRADED"  # Readable but imperfect (shadows, slight blur)
    UNREADABLE = "UNREADABLE"  # Cannot be parsed
    UNKNOWN = "UNKNOWN"


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
    """

    file_id: str
    file_name: str
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    declared_type: Optional[DocumentType] = None  # What the member says it is
    detected_type: Optional[DocumentType] = None   # What the system identifies
    quality: DocumentQuality = DocumentQuality.UNKNOWN
    content_url: Optional[str] = None              # Presigned/storage URL
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


class Claim(BaseModel):
    """
    Full claim record, enriched through pipeline processing.
    Combines the original submission with processed data.
    """

    claim_id: str = Field(default_factory=lambda: f"CLM-{uuid4().hex[:8].upper()}")
    submission: ClaimSubmission
    status: ClaimStatus = ClaimStatus.SUBMITTED
    documents: List[Document] = Field(default_factory=list)
    member: Optional[Member] = None
    decision: Optional[ClaimDecision] = None
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
