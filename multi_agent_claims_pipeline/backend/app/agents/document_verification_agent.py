"""
DocumentVerificationAgent — the second pipeline stage.

Determines what document types were submitted, what's required for the
claim category (from PolicyRepository, never hardcoded), what's missing,
what's unreadable, and whether processing can continue.

For each document, uses a pre-supplied DocumentClassification if one was
given (evaluation fixtures / already-classified real documents); otherwise
calls the injected AIProvider to classify it. This is the one place in
Phase 2A that makes a real AI call — and the only place that logic lives,
so the "fixture vs. real" distinction never leaks into CrossDocumentValidationAgent
or ClaimsPipeline.
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

from app.agents.base_agent import BaseAgent
from app.ai.prompts.document_verification import build_document_classification_request
from app.domain.errors import ExtractionError
from app.domain.models import DocumentMetadata, DocumentQuality, DocumentType
from app.domain.verification import (
    DocumentClassification,
    DocumentVerificationResult,
    DocumentVerificationStatus,
)
from app.policy.policy_repository import PolicyRepository

_UNREADABLE_QUALITIES = {DocumentQuality.UNREADABLE, DocumentQuality.PARTIAL}


def _label(document_type: DocumentType) -> str:
    """DocumentType -> human-readable label, e.g. HOSPITAL_BILL -> 'Hospital Bill'."""
    return document_type.value.replace("_", " ").title()


class DocumentVerificationAgent(BaseAgent):
    def __init__(
        self,
        *,
        ai_provider,
        policy_repository: PolicyRepository,
        agent_name: Optional[str] = None,
    ) -> None:
        super().__init__(ai_provider=ai_provider, agent_name=agent_name)
        self._policy_repository = policy_repository

    async def run(
        self,
        *,
        claim_category,
        documents: List[DocumentMetadata],
        classifications: Optional[Dict[str, DocumentClassification]] = None,
    ) -> DocumentVerificationResult:
        classifications = classifications or {}

        resolved: List[DocumentClassification] = []
        for doc in documents:
            existing = classifications.get(doc.file_id)
            if existing is not None:
                resolved.append(existing)
            else:
                resolved.append(await self._classify_via_ai(doc))

        requirement = self._policy_repository.get_document_requirements(claim_category)
        received_types = [c.document_type for c in resolved]
        received_set = set(received_types)

        missing = [t for t in requirement.required if t not in received_set]
        allowed_types = set(requirement.required) | set(requirement.optional)
        wrong = [c.document_type for c in resolved if c.document_type not in allowed_types]
        quality_issues = [c for c in resolved if c.quality in _UNREADABLE_QUALITIES]

        if quality_issues:
            status = DocumentVerificationStatus.NEEDS_RESUBMISSION
        elif missing:
            status = DocumentVerificationStatus.BLOCKED
        else:
            status = DocumentVerificationStatus.PASS

        message = self._build_message(
            claim_category=claim_category,
            received_types=received_types,
            missing=missing,
            wrong=wrong,
            quality_issues=quality_issues,
        )

        confidences = [c.confidence for c in resolved if c.confidence is not None]
        overall_confidence = min(confidences) if confidences else None

        return DocumentVerificationResult(
            status=status,
            required_documents=requirement.required,
            received_documents=received_types,
            missing_documents=missing,
            wrong_documents=wrong,
            quality_issues=quality_issues,
            classifications=resolved,
            user_message=message,
            confidence=overall_confidence,
        )

    # ── AI Classification ─────────────────────────────────────────────────────

    async def _classify_via_ai(self, doc: DocumentMetadata) -> DocumentClassification:
        """
        Classify a document that has no pre-supplied ground truth.

        Phase 2A has no file-upload/OCR pipeline yet, so this is a
        text-only classification from the filename and declared type — see
        app/ai/prompts/document_verification.py for why, and what changes
        when real uploads land.
        """
        request = build_document_classification_request(
            file_name=doc.file_name,
            declared_type=doc.declared_type,
        )
        response = await self.ai_provider.generate_structured(request)

        try:
            data = response.data
            document_type = DocumentType(data["document_type"])
            quality = DocumentQuality(data["quality"])
            patient_name = data.get("patient_name") or None
            confidence = data.get("confidence")
        except (KeyError, ValueError) as exc:
            raise ExtractionError(doc.file_id, f"could not parse AI classification: {exc}") from exc

        return DocumentClassification(
            file_id=doc.file_id,
            document_type=document_type,
            quality=quality,
            patient_name=patient_name,
            confidence=confidence,
            source="ai",
        )

    # ── Message Generation ────────────────────────────────────────────────────

    def _build_message(
        self,
        *,
        claim_category,
        received_types: List[DocumentType],
        missing: List[DocumentType],
        wrong: List[DocumentType],
        quality_issues: List[DocumentClassification],
    ) -> str:
        """
        Generate a specific, actionable message from structured results.
        Never a hardcoded per-case string — built the same way for any
        category/document combination.
        """
        if quality_issues:
            parts = []
            for c in quality_issues:
                reason = "could not be read" if c.quality == DocumentQuality.UNREADABLE else "was only partially readable"
                parts.append(f"Your {_label(c.document_type)} {reason}.")
            parts.append("Please re-upload the affected document(s) so we can continue processing your claim.")
            return " ".join(parts)

        if missing:
            uploaded_desc = _describe_counts(received_types)
            missing_desc = ", ".join(_label(t) for t in missing)
            return (
                f"You uploaded: {uploaded_desc}. "
                f"A {missing_desc} is required for a {claim_category.value.title()} claim, "
                f"but it was not found. Please upload your {missing_desc.lower()} to continue."
            )

        if wrong:
            wrong_desc = ", ".join(_label(t) for t in sorted(set(wrong), key=lambda t: t.value))
            return (
                f"We received a {wrong_desc} which is not required for a "
                f"{claim_category.value.title()} claim. Please double-check your upload — "
                "no action is needed if the required documents were also included."
            )

        return "All required documents were received and appear readable."


def _describe_counts(document_types: List[DocumentType]) -> str:
    """[PRESCRIPTION, PRESCRIPTION] -> '2 Prescription documents'."""
    if not document_types:
        return "no documents"
    counts = Counter(document_types)
    parts = []
    for doc_type, count in counts.items():
        noun = "document" if count == 1 else "documents"
        parts.append(f"{count} {_label(doc_type)} {noun}")
    return ", ".join(parts)
