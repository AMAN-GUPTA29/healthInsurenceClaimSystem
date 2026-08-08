"""
DocumentInputAdapter — the single input boundary for claim documents.

Both a real API caller and the evaluation runner (converting
test_cases.json fixtures) submit the *same* request shape:
ClaimSubmissionRequest. A real caller only ever fills in the fields a
member would actually know (file_id, file_name, declared_type). The
evaluation fixtures additionally carry ground-truth fields
(actual_type/quality/patient_name_on_doc) that stand in for what a real
AI classification would have produced — this adapter is what tells those
two apart and produces one common internal representation:

    ClaimSubmissionRequest -> (ClaimSubmission, {file_id: DocumentClassification})

DocumentVerificationAgent then uses a pre-supplied classification when one
exists in that dict, and falls back to a real AI call otherwise (see
app/agents/document_verification_agent.py). This adapter contains no
business rules of its own (no "is this claim valid" logic) and no
knowledge of specific test cases — it is purely a shape conversion, kept
outside every business agent as required.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.domain.models import (
    ClaimCategory,
    ClaimHistoryItem,
    ClaimSubmission,
    DocumentMetadata,
    DocumentQuality,
    DocumentType,
)
from app.domain.verification import DocumentClassification
from datetime import date


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


class DocumentInputAdapter:
    """Converts ClaimSubmissionRequest into the pipeline's internal input shape."""

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
