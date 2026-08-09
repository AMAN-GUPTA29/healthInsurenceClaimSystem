"""
Claim validation / document verification / cross-document validation
domain models.

Kept in a separate module from app/domain/models.py (which holds the core
Claim/Member/Document data model) — same pattern as app/domain/trace.py.
These are the strongly typed *results* the Phase 2A pipeline stages
produce; they never appear as raw dicts across a component boundary.

Rules (same as the rest of app/domain/):
- Pure Pydantic models. No database imports. No FastAPI imports. No AI SDK imports.
- No component may invent free-form status strings — use the enums here.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from app.domain.models import DocumentQuality, DocumentType
from app.domain.trace import AITraceMetadata

# ── Claim Validation ─────────────────────────────────────────────────────────


class ValidationIssue(BaseModel):
    """One structured problem found during claim validation."""

    code: str
    message: str
    field: Optional[str] = None
    recoverable: bool = False


class ValidationResult(BaseModel):
    """Output of ClaimValidationAgent."""

    valid: bool
    errors: List[ValidationIssue] = Field(default_factory=list)
    warnings: List[ValidationIssue] = Field(default_factory=list)


# ── Document Classification (AI or fixture-provided) ─────────────────────────


class DocumentClassification(BaseModel):
    """
    The classification/extraction result for one document — whatever
    determined the document's actual type, quality, and (if visible)
    patient name.

    `source` records provenance: "ai" means DocumentVerificationAgent
    called the configured AIProvider to produce this; "fixture" means the
    evaluation input adapter supplied it as ground truth (standing in for
    what a real classification would have found). This is purely for
    trace/debug transparency — downstream logic treats both identically.
    """

    file_id: str
    document_type: DocumentType
    quality: DocumentQuality = DocumentQuality.GOOD
    patient_name: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    source: str = "ai"  # "ai" | "fixture"


# ── Document Verification ─────────────────────────────────────────────────────


class DocumentVerificationStatus(str, Enum):
    """Controlled outcome vocabulary for DocumentVerificationAgent."""

    PASS = "PASS"
    BLOCKED = "BLOCKED"
    NEEDS_RESUBMISSION = "NEEDS_RESUBMISSION"


class DocumentVerificationResult(BaseModel):
    """Output of DocumentVerificationAgent."""

    status: DocumentVerificationStatus
    required_documents: List[DocumentType] = Field(default_factory=list)
    received_documents: List[DocumentType] = Field(default_factory=list)
    missing_documents: List[DocumentType] = Field(default_factory=list)
    wrong_documents: List[DocumentType] = Field(default_factory=list)
    quality_issues: List[DocumentClassification] = Field(default_factory=list)
    classifications: List[DocumentClassification] = Field(default_factory=list)
    user_message: str = ""
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    ai_calls: List[AITraceMetadata] = Field(
        default_factory=list,
        description="One entry per real AI classification call made (empty if every "
        "document had a pre-supplied classification — fixture or already-known).",
    )


# ── Cross-Document Validation ─────────────────────────────────────────────────


class CrossDocumentValidationStatus(str, Enum):
    """Controlled outcome vocabulary for CrossDocumentValidationAgent."""

    PASS = "PASS"
    BLOCKED = "BLOCKED"


class CrossDocumentValidationResult(BaseModel):
    """Output of CrossDocumentValidationAgent."""

    status: CrossDocumentValidationStatus
    patient_names: dict[str, str] = Field(
        default_factory=dict,
        description="Document type label -> patient name, for every document with an identifiable name.",
    )
    user_message: str = ""
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
