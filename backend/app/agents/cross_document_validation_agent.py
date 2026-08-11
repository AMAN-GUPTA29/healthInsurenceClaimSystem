"""
CrossDocumentValidationAgent — the third pipeline stage.

Checks consistency *across* already-classified documents, AND — since the
Phase 2A identity-validation gap fix — consistency *between* those
documents and the claim's actual member. Phase 2A only implements
patient-identity consistency (the checks TC003 and the identity-fix need)
— deliberately not a general validation engine yet.

Purely deterministic: it compares patient names DocumentVerificationAgent
(or the fixture adapter) already extracted against each other and against
the `Member` `ClaimValidationAgent` already resolved. It never calls AI
itself and never re-reads a document.

Identity-fix background (see docs/AI_HANDOFF.md "Phase 2A identity-
validation gap fixed" for the full account): before this fix, two
documents that agreed with *each other* always passed, even if neither
belonged to the claim's actual member — e.g. member EMP001 (Rajesh Kumar)
submitting two documents both for "Vikram Joshi" would incorrectly PASS,
because the only comparison was document-to-document. This agent now
performs both:
    (a) document <-> document  (existing, unchanged, checked first)
    (b) document <-> claim member  (new — only reachable once (a) already
        agrees, since a member mismatch on top of an internal document
        mismatch doesn't need a second, different message)

Both outcomes deliberately still return a structured
`CrossDocumentValidationResult(status=BLOCKED)`, never raise
`DocumentPatientMismatchError` (app/domain/errors.py) — consistent with
the existing (a) check and with Decision 19 in docs/AI_HANDOFF.md: a
correctly-detected "blocked" verdict is the agent succeeding at its job,
not a failure, so it must stay a return value, not an exception. Raising
here would incorrectly route through ClaimsPipeline's FAILED/degrade path
instead of the normal early-stop path.
"""

from __future__ import annotations

import re
from typing import List, Optional

from app.agents.base_agent import BaseAgent
from app.domain.models import Member
from app.domain.verification import (
    CrossDocumentValidationResult,
    CrossDocumentValidationStatus,
    DocumentClassification,
)


def _normalize_name(name: str) -> str:
    """Case/whitespace-insensitive comparison key. Not fuzzy matching —
    see docs/component-contracts.md for why that's a documented limitation.
    Used for both document<->document and (identity fix) document<->member
    comparisons, so "Rajesh Kumar" / "rajesh kumar" / " Rajesh Kumar " are
    all the same identity in both checks."""
    return re.sub(r"\s+", " ", name.strip()).lower()


def _label(document_type) -> str:
    return document_type.value.replace("_", " ").title()


class CrossDocumentValidationAgent(BaseAgent):
    def __init__(self, *, agent_name: str | None = None) -> None:
        super().__init__(ai_provider=None, agent_name=agent_name)

    async def run(
        self,
        classifications: List[DocumentClassification],
        *,
        member: Optional[Member] = None,
    ) -> CrossDocumentValidationResult:
        """
        `member` is optional and defaults to None so every existing caller
        (evaluation runner fixtures, older tests) that doesn't have one
        keeps working unmodified — the document<->member check below is
        simply skipped when it's not supplied, exactly as if this
        parameter didn't exist.
        """
        named = [c for c in classifications if c.patient_name]

        if not named:
            return CrossDocumentValidationResult(
                status=CrossDocumentValidationStatus.PASS,
                patient_names={},
                user_message="Not enough documents with an identifiable patient name to cross-check.",
            )

        patient_names = {_label(c.document_type): c.patient_name for c in named}
        distinct = {_normalize_name(c.patient_name) for c in named}
        confidences = [c.confidence for c in named if c.confidence is not None]
        overall_confidence = min(confidences) if confidences else None

        # (a) Document <-> document — existing check, unchanged, still
        # takes priority: if the documents don't even agree with each
        # other, that's the message, regardless of who the member is.
        if len(distinct) > 1:
            detail = "; ".join(f"{label}: {name}" for label, name in patient_names.items())
            message = (
                "The documents appear to belong to different patients. "
                f"{detail}. Please upload documents belonging to the same patient."
            )
            return CrossDocumentValidationResult(
                status=CrossDocumentValidationStatus.BLOCKED,
                patient_names=patient_names,
                user_message=message,
                confidence=overall_confidence,
            )

        # (b) Document <-> claim member (identity-fix) — the documents
        # agree with each other (or there's exactly one named document),
        # so now check that shared identity against who this claim is
        # actually for. A member conceptually maps to
        # RejectionReason.PATIENT_NOT_MEMBER (app/domain/models.py) — that
        # enum isn't attached to a field here because Phase 2A has no
        # ClaimDecision yet (rejection_reasons lives there, Phase 3); this
        # result's existing `status`/`user_message` already carries the
        # same signal a member-facing message needs today.
        if member is not None and member.name:
            document_identity = next(iter(distinct))
            if _normalize_name(member.name) != document_identity:
                detected_name = named[0].patient_name
                message = (
                    f"The uploaded documents identify the patient as {detected_name}, but this "
                    f"claim is for {member.name} ({member.member_id}). Please upload documents "
                    "belonging to the covered member."
                )
                return CrossDocumentValidationResult(
                    status=CrossDocumentValidationStatus.BLOCKED,
                    patient_names=patient_names,
                    user_message=message,
                    confidence=overall_confidence,
                )

        if len(named) < 2:
            return CrossDocumentValidationResult(
                status=CrossDocumentValidationStatus.PASS,
                patient_names=patient_names,
                user_message="Not enough documents with an identifiable patient name to cross-check.",
            )

        return CrossDocumentValidationResult(
            status=CrossDocumentValidationStatus.PASS,
            patient_names=patient_names,
            user_message="All documents belong to the same patient.",
        )
