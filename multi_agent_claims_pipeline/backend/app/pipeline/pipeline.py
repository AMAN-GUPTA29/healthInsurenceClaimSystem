"""
Claims Pipeline — Phase 2A: claim validation, document verification,
cross-document validation, and early stopping. No policy/decision logic
yet — see app/policy/policy_engine.py and this module's own docstring
history for what's still planned.

Planned pipeline stages (full, across all phases):
    1.  ClaimValidationAgent         — ✅ Phase 2A
    2.  DocumentVerificationAgent    — ✅ Phase 2A
    3.  CrossDocumentValidationAgent — ✅ Phase 2A (patient-identity only)
    4.  DocumentExtractionAgent      — ✅ Phase 2B (structured data per document type)
    5.  PolicyEvaluationEngine       — (planned) deterministic policy rules
    6.  FraudAnalysisAgent           — (planned)
    7.  FinancialCalculationService  — (planned) copay, network discount, limits
    8.  DecisionGenerationAgent      — (planned)
    9.  ExplanationAgent             — (planned)
    10. TraceRecorder                — ✅ Phase 1 (TraceService, injected below)

Phase 2B note — why extraction runs after cross-document validation, not
before or in parallel: extraction is the most expensive stage (real
multimodal AI calls per document, ~20-40s each) and the least reversible
signal to explain to a member ("here's what we read from your documents").
Running it only after both early-stop checks already passed means a claim
that would have stopped anyway (wrong document, unreadable document,
patient mismatch) never pays that cost — see docs/architecture.md
"Document Extraction" for the full rationale.

Design — how early stopping is represented in the trace:
Each stage's own STARTED/COMPLETED/FAILED pair reflects whether the agent
*ran without error* — a document-verification agent that correctly finds
a missing document has NOT failed, it succeeded at its job, so that's a
COMPLETED event (with the verdict captured in its metadata), never FAILED.
FAILED is reserved for genuine infrastructure/AI problems (see
_run_stage's except-block). Skipped downstream stages get an explicit
SKIPPED event. Exactly one PIPELINE-component event summarizes the run's
outcome: COMPLETED (reached the end of what Phase 2A implements), WARNING
(stopped early for an expected business reason), or FAILED (stopped
because a stage genuinely errored). Full rationale in docs/architecture.md.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Coroutine, Dict, Optional, TypeVar

from app.agents.claim_validation_agent import ClaimValidationAgent
from app.agents.cross_document_validation_agent import CrossDocumentValidationAgent
from app.agents.document_extraction_agent import DocumentExtractionAgent
from app.agents.document_verification_agent import DocumentVerificationAgent
from app.domain.models import Claim, ClaimStatus, DocumentProcessingStatus
from app.domain.trace import TraceComponent
from app.domain.verification import (
    CrossDocumentValidationStatus,
    DocumentClassification,
    DocumentVerificationStatus,
)
from app.tracing.service import TraceService

T = TypeVar("T")


def _final_user_message(claim: Claim) -> str:
    """
    The member-facing message once every configured stage has run without a
    hard stop. Built from the structured extraction_result the same way
    DocumentVerificationAgent builds its own message from structured
    results (see docs/AI_HANDOFF.md invariant #17) — never a hardcoded
    per-case string.
    """
    result = claim.extraction_result
    if result is None:
        return "All early checks passed. Policy evaluation is not yet implemented."
    if result.has_failures:
        return (
            "All early checks passed. We extracted the information we could from your "
            f"documents, but {len(result.failures)} document(s) could not be fully processed "
            "— a team member may need to review this claim manually. Policy evaluation is "
            "not yet implemented."
        )
    return "All early checks passed and document information was extracted. Policy evaluation is not yet implemented."


class ClaimsPipeline:
    """Orchestrates the Phase 2A pipeline stages for a single claim."""

    def __init__(
        self,
        *,
        claim_validation_agent: ClaimValidationAgent,
        document_verification_agent: DocumentVerificationAgent,
        cross_document_validation_agent: CrossDocumentValidationAgent,
        document_extraction_agent: Optional[DocumentExtractionAgent] = None,
    ) -> None:
        self._claim_validation_agent = claim_validation_agent
        self._document_verification_agent = document_verification_agent
        self._cross_document_validation_agent = cross_document_validation_agent
        # Optional, defaulting to None, so every existing caller that builds
        # a ClaimsPipeline without an extraction agent (evaluation runner,
        # existing tests) keeps working unmodified — DOCUMENT_EXTRACTION is
        # simply recorded SKIPPED rather than attempted. See `run()`.
        self._document_extraction_agent = document_extraction_agent

    async def run(
        self,
        claim: Claim,
        *,
        classifications: Optional[Dict[str, DocumentClassification]] = None,
        tracer: TraceService,
    ) -> Claim:
        """
        Run claim through claim validation -> document verification ->
        cross-document validation, stopping early on the first blocking
        outcome. Mutates and returns `claim` with the results attached.

        Guarantee: never raises. A genuine stage failure (AI timeout, parse
        error, etc. — as opposed to an expected "blocked"/"needs
        resubmission" business verdict) is caught, recorded in the trace as
        FAILED, and reflected back as a degraded Claim (status=BLOCKED,
        user_message explaining a technical error occurred) rather than
        propagating — see module docstring and docs/architecture.md for
        why this matters for the assignment's graceful-failure requirement.
        """
        classifications = classifications or {}
        claim.trace_id = tracer.context.trace_id

        t0 = time.monotonic()
        await tracer.started(TraceComponent.PIPELINE, "Claim processing started")

        # ── Stage 1: Claim Validation ────────────────────────────────────────
        try:
            validation_result = await self._run_stage(
                tracer,
                TraceComponent.CLAIM_VALIDATION,
                lambda: self._claim_validation_agent.run(claim.submission),
                metadata_fn=lambda r: {
                    "valid": r.valid,
                    "error_codes": [e.code for e in r.errors],
                },
            )
        except Exception as exc:
            return await self._degrade(
                claim, tracer, TraceComponent.CLAIM_VALIDATION, exc, t0,
                remaining=[TraceComponent.DOCUMENT_VERIFICATION, TraceComponent.CROSS_DOCUMENT_VALIDATION],
            )
        claim.validation_result = validation_result

        if not validation_result.valid:
            reason = "; ".join(e.message for e in validation_result.errors) or "claim validation failed"
            await tracer.skipped(
                TraceComponent.DOCUMENT_VERIFICATION, "Skipped — claim validation failed"
            )
            await tracer.skipped(
                TraceComponent.CROSS_DOCUMENT_VALIDATION, "Skipped — claim validation failed"
            )
            await tracer.warning(
                TraceComponent.PIPELINE,
                f"Stopped: {reason}",
                metadata={"stopped_at": TraceComponent.CLAIM_VALIDATION.value},
            )
            claim.status = ClaimStatus.BLOCKED
            claim.stopped_at = TraceComponent.CLAIM_VALIDATION.value
            claim.user_message = reason
            claim.processing_time_ms = (time.monotonic() - t0) * 1000
            return claim

        # ── Stage 2: Document Verification ───────────────────────────────────
        try:
            doc_result = await self._run_stage(
                tracer,
                TraceComponent.DOCUMENT_VERIFICATION,
                lambda: self._document_verification_agent.run(
                    claim_category=claim.submission.claim_category,
                    documents=claim.submission.documents,
                    classifications=classifications,
                ),
                metadata_fn=lambda r: {
                    "status": r.status.value,
                    "missing_documents": [t.value for t in r.missing_documents],
                    "wrong_documents": [t.value for t in r.wrong_documents],
                    "quality_issue_count": len(r.quality_issues),
                    "ai_calls_made": len(r.ai_calls),
                },
                confidence_fn=lambda r: r.confidence,
                ai_metadata_fn=lambda r: r.ai_calls[0] if r.ai_calls else None,
            )
        except Exception as exc:
            return await self._degrade(
                claim, tracer, TraceComponent.DOCUMENT_VERIFICATION, exc, t0,
                remaining=[TraceComponent.CROSS_DOCUMENT_VALIDATION],
            )
        claim.document_verification_result = doc_result
        self._apply_classifications(claim, doc_result.classifications)

        if doc_result.status != DocumentVerificationStatus.PASS:
            await tracer.skipped(
                TraceComponent.CROSS_DOCUMENT_VALIDATION,
                f"Skipped — document verification {doc_result.status.value.lower()}",
            )
            await tracer.warning(
                TraceComponent.PIPELINE,
                f"Stopped: document verification {doc_result.status.value.lower()}",
                metadata={
                    "stopped_at": TraceComponent.DOCUMENT_VERIFICATION.value,
                    "status": doc_result.status.value,
                },
            )
            claim.status = (
                ClaimStatus.DOCUMENTS_PENDING
                if doc_result.status == DocumentVerificationStatus.NEEDS_RESUBMISSION
                else ClaimStatus.BLOCKED
            )
            claim.stopped_at = TraceComponent.DOCUMENT_VERIFICATION.value
            claim.user_message = doc_result.user_message
            claim.processing_time_ms = (time.monotonic() - t0) * 1000
            return claim

        # ── Stage 3: Cross-Document Validation ──────────────────────────────
        try:
            cross_result = await self._run_stage(
                tracer,
                TraceComponent.CROSS_DOCUMENT_VALIDATION,
                lambda: self._cross_document_validation_agent.run(doc_result.classifications),
                metadata_fn=lambda r: {"status": r.status.value, "patient_names": r.patient_names},
                confidence_fn=lambda r: r.confidence,
            )
        except Exception as exc:
            return await self._degrade(
                claim, tracer, TraceComponent.CROSS_DOCUMENT_VALIDATION, exc, t0,
                remaining=[TraceComponent.DOCUMENT_EXTRACTION],
            )
        claim.cross_document_validation_result = cross_result

        if cross_result.status != CrossDocumentValidationStatus.PASS:
            await tracer.skipped(
                TraceComponent.DOCUMENT_EXTRACTION,
                "Skipped — cross-document validation failed",
            )
            await tracer.warning(
                TraceComponent.PIPELINE,
                "Stopped: cross-document validation failed",
                metadata={"stopped_at": TraceComponent.CROSS_DOCUMENT_VALIDATION.value},
            )
            claim.status = ClaimStatus.BLOCKED
            claim.stopped_at = TraceComponent.CROSS_DOCUMENT_VALIDATION.value
            claim.user_message = cross_result.user_message
            claim.processing_time_ms = (time.monotonic() - t0) * 1000
            return claim

        # ── Stage 4: Document Extraction (Phase 2B) ─────────────────────────
        if self._document_extraction_agent is None:
            # No extraction agent configured (e.g. the evaluation runner,
            # which never has real document bytes for a fixture-driven
            # case) — not an error, just nothing to do this run.
            await tracer.skipped(
                TraceComponent.DOCUMENT_EXTRACTION,
                "Skipped — no extraction agent configured for this pipeline",
            )
        else:
            try:
                extraction_result = await self._run_stage(
                    tracer,
                    TraceComponent.DOCUMENT_EXTRACTION,
                    lambda: self._document_extraction_agent.run(documents=claim.submission.documents),
                    metadata_fn=lambda r: {
                        "documents_extracted": len(r.extractions),
                        "failures": len(r.failures),
                        "skipped": len(r.skipped),
                        "has_failures": r.has_failures,
                    },
                    confidence_fn=lambda r: r.confidence,
                    ai_metadata_fn=lambda r: r.ai_calls[0] if r.ai_calls else None,
                )
            except Exception as exc:
                return await self._degrade(
                    claim, tracer, TraceComponent.DOCUMENT_EXTRACTION, exc, t0, remaining=[],
                )
            claim.extraction_result = extraction_result

        # ── End of Phase 2B ───────────────────────────────────────────────────
        await tracer.completed(
            TraceComponent.PIPELINE,
            message="Reached end of Phase 2B pipeline — policy evaluation and decision are not yet implemented",
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        claim.status = ClaimStatus.PROCESSING
        claim.stopped_at = None
        claim.user_message = _final_user_message(claim)
        claim.processing_time_ms = (time.monotonic() - t0) * 1000
        return claim

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _apply_classifications(claim: Claim, classifications) -> None:
        """
        Write DocumentVerificationAgent's findings back onto claim.documents
        (detected_type/quality/patient_name/confidence) so the API response
        and persisted rows reflect what was actually determined about each
        document — not just what the member declared (a real upload never
        declares a type at all; the AI's read of the actual file is the
        only source of truth — see DocumentMetadata's docstring).

        Documents with no matching classification (shouldn't normally
        happen — every submitted document is either pre-classified or sent
        to the AI) are left at their default PENDING processing_status.
        """
        by_file_id = {c.file_id: c for c in classifications}
        for doc in claim.documents:
            classification = by_file_id.get(doc.metadata.file_id)
            if classification is not None:
                doc.metadata.detected_type = classification.document_type
                doc.metadata.quality = classification.quality
                doc.metadata.patient_name = classification.patient_name
                doc.metadata.confidence = classification.confidence
                doc.metadata.processing_status = DocumentProcessingStatus.PROCESSED

    async def _degrade(
        self,
        claim: Claim,
        tracer: TraceService,
        failed_component: TraceComponent,
        exc: Exception,
        t0: float,
        *,
        remaining: list[TraceComponent],
    ) -> Claim:
        """
        Convert a genuine stage failure into a degraded (but still
        returned, never raised) Claim. `_run_stage` has already recorded
        the FAILED event for `failed_component`; this records SKIPPED for
        anything downstream and one PIPELINE-level FAILED event, then
        returns a claim an ops user can still look up and understand.
        """
        if failed_component == TraceComponent.DOCUMENT_VERIFICATION:
            for doc in claim.documents:
                if doc.metadata.processing_status == DocumentProcessingStatus.PENDING:
                    doc.metadata.processing_status = DocumentProcessingStatus.FAILED

        for component in remaining:
            await tracer.skipped(
                component, f"Skipped — {failed_component.value} failed with a technical error"
            )
        await tracer.failed(
            TraceComponent.PIPELINE,
            exc,
            message=f"Stopped: {failed_component.value} failed unexpectedly",
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        claim.status = ClaimStatus.BLOCKED
        claim.stopped_at = failed_component.value
        claim.user_message = (
            "We hit a technical problem while processing your claim and could not finish "
            f"{failed_component.value.replace('_', ' ').title()}. Your claim has been saved — "
            "please try again later or contact support if this continues."
        )
        claim.processing_time_ms = (time.monotonic() - t0) * 1000
        return claim

    async def _run_stage(
        self,
        tracer: TraceService,
        component: TraceComponent,
        coro_factory: Callable[[], Coroutine[Any, Any, T]],
        *,
        metadata_fn: Callable[[T], Dict[str, Any]],
        confidence_fn: Optional[Callable[[T], Optional[float]]] = None,
        ai_metadata_fn: Optional[Callable[[T], Optional[Any]]] = None,
    ) -> T:
        """
        Run one stage with STARTED/COMPLETED-with-metadata/FAILED trace
        events. A stage that runs to completion (even with a "blocked"
        business verdict) is COMPLETED; only an actual raised exception
        (AI timeout, parse error, etc.) is FAILED.

        `ai_metadata_fn`, when given, extracts an `AITraceMetadata` from the
        stage result to attach to the COMPLETED event — used only by
        DOCUMENT_VERIFICATION, the one stage that can make a real AI call.
        """
        t0 = time.monotonic()
        await tracer.started(component)
        try:
            result = await coro_factory()
        except Exception as exc:
            await tracer.failed(component, exc, duration_ms=(time.monotonic() - t0) * 1000)
            raise
        else:
            await tracer.completed(
                component,
                duration_ms=(time.monotonic() - t0) * 1000,
                metadata=metadata_fn(result),
                confidence=confidence_fn(result) if confidence_fn else None,
                ai_metadata=ai_metadata_fn(result) if ai_metadata_fn else None,
            )
            return result
