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

No-legible-name gap fix: a real classification pass (source="ai") used to
require only *one* document to carry a legible, matching patient name —
any other document with no readable name at all rode along unverified,
as long as it didn't actively disagree with the one that was readable.
That's backwards for an insurance claim: a hospital bill with no visible
patient name is exactly the document whose payable amount is about to be
approved, and "some other document had a name" doesn't establish that
THIS bill belongs to this member. Every document — not just one — must
now show a legible name that matches the claim's member (when a real
classification ran and the member is known), or the claim BLOCKS asking
for a version that clearly shows the patient's name; the same
conservative "can't verify -> don't assume" stance already used
elsewhere (see docs/tradeoffs.md "Network Hospital Matching"). A
detected identity *conflict* (documents disagreeing with each other, or
agreeing on the wrong person) is still reported first and takes priority
over "some document has no name" — a specific wrong-identity finding is
more actionable than a generic "can't confirm" one. Evaluation fixtures
(source="fixture") are deliberately exempt: DocumentInputAdapter never
populates patient_name_on_doc unless the specific test case is
exercising identity matching, so fixture data having no name reflects
the fixture's own scope, not a real "AI found nothing" signal.

Identity-fix background: before this fix, two
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
the existing (a) check: a
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
    a deliberate, documented limitation (see docs/tradeoffs.md "Network
    Hospital Matching" for the shared reasoning against fuzzy matching).
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
        unnamed = [c for c in classifications if not c.patient_name]
        real_classification_attempted = any(c.source == "ai" for c in classifications)
        member_known = member is not None and bool(member.name)

        if not named:
            if real_classification_attempted and member_known:
                return CrossDocumentValidationResult(
                    status=CrossDocumentValidationStatus.BLOCKED,
                    patient_names={},
                    user_message=(
                        "None of the uploaded documents show a legible patient name, so we "
                        f"can't confirm they belong to {member.name} ({member.member_id}). "
                        "Please upload a document that clearly shows the patient's name."
                    ),
                )
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
        if member_known:
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

        # (c) Every-document gap fix — the named document(s) agree with
        # each other and with the member, but at least one OTHER document
        # (real classification, not a fixture) still has no legible name
        # at all. A confirmed match elsewhere doesn't vouch for a document
        # that couldn't be identified — the unverified one could belong to
        # anyone. Only reachable once (a)/(b) already found no conflict,
        # since a specific wrong-identity finding is more actionable than
        # this generic "can't confirm every document" one.
        if real_classification_attempted and member_known and unnamed:
            missing = ", ".join(_label(c.document_type) for c in unnamed)
            message = (
                f"{missing} does not show a legible patient name, so we can't confirm "
                f"every uploaded document belongs to {member.name} ({member.member_id}). "
                "Please upload a version that clearly shows the patient's name."
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
