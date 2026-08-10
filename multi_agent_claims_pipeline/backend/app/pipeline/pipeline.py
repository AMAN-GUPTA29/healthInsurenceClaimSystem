"""
Claims Pipeline — orchestrates claim validation, document verification,
cross-document (member-identity) validation, document extraction, policy
evaluation, financial calculation, and fraud analysis, with early stopping
on Phase 2A's stages and graceful degradation everywhere. No final
decision logic yet — see app/agents/ (planned, Phase 2D) for what's still
missing.

Planned pipeline stages (full, across all phases):
    1.  ClaimValidationAgent          — ✅ Phase 2A
    2.  DocumentVerificationAgent     — ✅ Phase 2A
    3.  CrossDocumentValidationAgent  — ✅ Phase 2A (patient-identity + member-identity, Phase 2A fix)
    4.  DocumentExtractionAgent       — ✅ Phase 2B (structured data per document type)
    5.  PolicyEngine                  — ✅ Phase 2C (deterministic policy rules)
    6.  FinancialCalculationService   — ✅ Phase 2C (copay, network discount, limits)
    7.  FraudAnalysisAgent            — ✅ Phase 2C (deterministic fraud thresholds)
    8.  DecisionGenerationAgent       — (planned, Phase 2D)
    9.  ExplanationAgent              — (planned, Phase 2D)
    10. TraceRecorder                 — ✅ Phase 1 (TraceService, injected below)

Phase 2B note — why extraction runs after cross-document validation, not
before or in parallel: extraction is the most expensive stage (real
multimodal AI calls per document, ~20-40s each) and the least reversible
signal to explain to a member ("here's what we read from your documents").
Running it only after both early-stop checks already passed means a claim
that would have stopped anyway (wrong document, unreadable document,
patient mismatch) never pays that cost — see docs/architecture.md
"Document Extraction" for the full rationale.

Phase 2C note — why Policy/Financial/Fraud use a different failure model
than stages 1-3: stages 1-3 are early-stop *gates* (an invalid claim or a
document problem means there's nothing useful to do next). Policy
evaluation, financial calculation, and fraud analysis are not gates — they
are *findings* for Phase 2D's DecisionGenerationAgent to weigh (a FAILED
policy rule doesn't mean "stop the pipeline", it means "tell Phase 2D
this rule failed"). So a genuine failure in one of these three stages
(AI/infra problem — none of them call AI in this phase, but the pattern
holds for any unexpected exception) is handled with `_run_soft_stage`:
record FAILED in the trace, leave the corresponding `claim.*_result` field
`None` (never a guessed/fabricated result — see docs/AI_HANDOFF.md
invariant re: PolicyEngine/FinancialCalculationService failure), and
*continue* to the next stage rather than blocking the claim. Financial
Calculation is the one dependency in this trio — it needs
PolicyEvaluationResult as input, so it's skipped (not attempted) if Policy
didn't produce one; Fraud Analysis has no such dependency and always
attempts to run independently, since "was this member submitting
suspiciously many claims" doesn't require a payable-amount figure.

Design — how early stopping is represented in the trace:
Each stage's own STARTED/COMPLETED/FAILED pair reflects whether the agent
*ran without error* — a document-verification agent that correctly finds
a missing document has NOT failed, it succeeded at its job, so that's a
COMPLETED event (with the verdict captured in its metadata), never FAILED.
FAILED is reserved for genuine infrastructure/AI problems (see
_run_stage's except-block). Skipped downstream stages get an explicit
SKIPPED event. Exactly one PIPELINE-component event summarizes the run's
outcome: COMPLETED (reached the end of what's implemented), WARNING
(stopped early for an expected business reason), or FAILED (stopped
because a Phase 2A/2B stage genuinely errored). Full rationale in
docs/architecture.md.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Coroutine, Dict, Optional, Tuple, TypeVar

from app.agents.claim_validation_agent import ClaimValidationAgent
from app.agents.cross_document_validation_agent import CrossDocumentValidationAgent
from app.agents.document_extraction_agent import DocumentExtractionAgent
from app.agents.document_verification_agent import DocumentVerificationAgent
from app.agents.fraud_analysis_agent import FraudAnalysisAgent
from app.domain.models import Claim, ClaimStatus, DocumentProcessingStatus
from app.domain.trace import TraceComponent
from app.domain.verification import (
    CrossDocumentValidationStatus,
    DocumentClassification,
    DocumentVerificationStatus,
)
from app.policy.policy_engine import PolicyEngine
from app.services.financial_calculation_service import FinancialCalculationService
from app.tracing.service import TraceService

T = TypeVar("T")

# Every stage that comes after a given stage, in pipeline order — used by
# `_degrade()` to mark the full remainder SKIPPED in the trace when an
# earlier stage genuinely fails, so an ops user sees explicit SKIPPED
# events for every downstream component rather than silent absence.
_PIPELINE_ORDER = [
    TraceComponent.CLAIM_VALIDATION,
    TraceComponent.DOCUMENT_VERIFICATION,
    TraceComponent.CROSS_DOCUMENT_VALIDATION,
    TraceComponent.DOCUMENT_EXTRACTION,
    TraceComponent.POLICY_ENGINE,
    TraceComponent.FINANCIAL_CALCULATION,
    TraceComponent.FRAUD_ANALYSIS,
]
_DOWNSTREAM_OF = {
    component: _PIPELINE_ORDER[i + 1 :] for i, component in enumerate(_PIPELINE_ORDER)
}


def _final_user_message(
    claim: Claim, *, policy_failed: bool, financial_failed: bool, fraud_failed: bool
) -> str:
    """
    The member-facing message once every configured stage has run without a
    hard stop. Built from the structured results the same way
    DocumentVerificationAgent builds its own message from structured
    results (see docs/AI_HANDOFF.md invariant #17) — never a hardcoded
    per-case string.
    """
    extraction_result = claim.extraction_result
    notes = []
    if extraction_result is not None and extraction_result.has_failures:
        notes.append(f"{len(extraction_result.failures)} document(s) could not be fully processed")
    if policy_failed:
        notes.append("policy evaluation could not be completed")
    if financial_failed:
        notes.append("the financial calculation could not be completed")
    if fraud_failed:
        notes.append("fraud analysis could not be completed")

    if notes:
        return (
            "All early checks passed, but " + "; ".join(notes) + " — a team member may need to "
            "review this claim manually. A final decision has not yet been made."
        )
    return "All early checks passed. Policy, financial, and fraud analysis are complete. A final decision has not yet been made."


async def _run_financial_calc(service: FinancialCalculationService, claim: Claim, policy_result) -> Any:
    """FinancialCalculationService.calculate() is a plain synchronous
    method (purely deterministic Decimal arithmetic, no I/O — see its own
    module docstring) — this thin async wrapper is only here so it fits
    `_run_soft_stage`'s `coro_factory` interface alongside the genuinely
    async Policy/Fraud stages."""
    return service.calculate(claim, policy_result)


class ClaimsPipeline:
    """Orchestrates the Phase 2A pipeline stages for a single claim."""

    def __init__(
        self,
        *,
        claim_validation_agent: ClaimValidationAgent,
        document_verification_agent: DocumentVerificationAgent,
        cross_document_validation_agent: CrossDocumentValidationAgent,
        document_extraction_agent: Optional[DocumentExtractionAgent] = None,
        policy_engine: Optional[PolicyEngine] = None,
        financial_calculation_service: Optional[FinancialCalculationService] = None,
        fraud_analysis_agent: Optional[FraudAnalysisAgent] = None,
    ) -> None:
        self._claim_validation_agent = claim_validation_agent
        self._document_verification_agent = document_verification_agent
        self._cross_document_validation_agent = cross_document_validation_agent
        # All optional, defaulting to None (same backward-compatibility
        # trick as document_extraction_agent, Decision 30), so every
        # existing caller that builds a ClaimsPipeline without them
        # (evaluation runner, existing tests) keeps working unmodified —
        # the corresponding stage is simply recorded SKIPPED. See `run()`.
        self._document_extraction_agent = document_extraction_agent
        self._policy_engine = policy_engine
        self._financial_calculation_service = financial_calculation_service
        self._fraud_analysis_agent = fraud_analysis_agent

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
                remaining=_DOWNSTREAM_OF[TraceComponent.CLAIM_VALIDATION],
            )
        claim.validation_result = validation_result
        # Identity-fix: carry the Member ClaimValidationAgent already
        # resolved forward onto the claim itself (Claim.member existed but
        # was previously always None — see docs/AI_HANDOFF.md "Phase 2A
        # identity-validation gap fixed"), so CrossDocumentValidationAgent
        # can check documents against it without a second PolicyRepository
        # lookup.
        claim.member = validation_result.member

        if not validation_result.valid:
            reason = "; ".join(e.message for e in validation_result.errors) or "claim validation failed"
            for component in _DOWNSTREAM_OF[TraceComponent.CLAIM_VALIDATION]:
                await tracer.skipped(component, "Skipped — claim validation failed")
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
                remaining=_DOWNSTREAM_OF[TraceComponent.DOCUMENT_VERIFICATION],
            )
        claim.document_verification_result = doc_result
        self._apply_classifications(claim, doc_result.classifications)

        if doc_result.status != DocumentVerificationStatus.PASS:
            for component in _DOWNSTREAM_OF[TraceComponent.DOCUMENT_VERIFICATION]:
                await tracer.skipped(
                    component, f"Skipped — document verification {doc_result.status.value.lower()}"
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
                lambda: self._cross_document_validation_agent.run(
                    doc_result.classifications, member=claim.member
                ),
                metadata_fn=lambda r: {
                    "status": r.status.value,
                    "patient_names": r.patient_names,
                    # Identity-fix: safe, structured signal for reconstructing
                    # *why* a BLOCKED verdict happened — never the member's
                    # full record, just the one field relevant here. See
                    # docs/AI_HANDOFF.md "Phase 2A identity-validation gap fixed".
                    "expected_member_name": claim.member.name if claim.member else None,
                },
                confidence_fn=lambda r: r.confidence,
            )
        except Exception as exc:
            return await self._degrade(
                claim, tracer, TraceComponent.CROSS_DOCUMENT_VALIDATION, exc, t0,
                remaining=_DOWNSTREAM_OF[TraceComponent.CROSS_DOCUMENT_VALIDATION],
            )
        claim.cross_document_validation_result = cross_result

        if cross_result.status != CrossDocumentValidationStatus.PASS:
            for component in _DOWNSTREAM_OF[TraceComponent.CROSS_DOCUMENT_VALIDATION]:
                await tracer.skipped(component, "Skipped — cross-document validation failed")
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
                    claim, tracer, TraceComponent.DOCUMENT_EXTRACTION, exc, t0,
                    remaining=_DOWNSTREAM_OF[TraceComponent.DOCUMENT_EXTRACTION],
                )
            claim.extraction_result = extraction_result

        # ── Stage 5: Policy Evaluation (Phase 2C) ───────────────────────────
        policy_failed = False
        if self._policy_engine is None:
            await tracer.skipped(
                TraceComponent.POLICY_ENGINE, "Skipped — no policy engine configured for this pipeline"
            )
        else:
            policy_result, policy_failed = await self._run_soft_stage(
                tracer,
                TraceComponent.POLICY_ENGINE,
                lambda: self._policy_engine.evaluate(claim),
                metadata_fn=lambda r: {
                    "covered": r.covered,
                    "rules_checked": len(r.findings),
                    "rules_passed": len(r.passed_rules),
                    "rules_failed": len(r.failed_rules),
                    "waiting_period": r.waiting_period_applies,
                    "exclusion": r.exclusion_applies,
                    "pre_auth": r.requires_pre_authorization,
                },
                confidence_fn=lambda r: r.confidence,
            )
            claim.policy_evaluation_result = policy_result

        # ── Stage 6: Financial Calculation (Phase 2C) ───────────────────────
        # Depends on Stage 5's output — skipped (not attempted) if Policy
        # Evaluation didn't produce a result, whether because it wasn't
        # configured or because it failed.
        financial_failed = False
        if claim.policy_evaluation_result is None:
            await tracer.skipped(
                TraceComponent.FINANCIAL_CALCULATION,
                "Skipped — no policy evaluation result available"
                if self._policy_engine is not None
                else "Skipped — no policy engine configured for this pipeline",
            )
        elif self._financial_calculation_service is None:
            await tracer.skipped(
                TraceComponent.FINANCIAL_CALCULATION,
                "Skipped — no financial calculation service configured for this pipeline",
            )
        else:
            policy_result_for_calc = claim.policy_evaluation_result
            financial_result, financial_failed = await self._run_soft_stage(
                tracer,
                TraceComponent.FINANCIAL_CALCULATION,
                lambda: _run_financial_calc(self._financial_calculation_service, claim, policy_result_for_calc),
                metadata_fn=lambda r: {
                    "claimed_amount": str(r.claimed_amount),
                    "eligible_amount": str(r.eligible_amount),
                    "sub_limit_applied": r.sub_limit_applied,
                    "per_claim_limit_applied": r.per_claim_limit_applied,
                    "copay_applied": bool(r.copay_percent),
                    "payable_amount": str(r.payable_amount),
                    "currency": r.currency,
                },
                confidence_fn=lambda r: r.confidence,
            )
            claim.financial_calculation_result = financial_result

        # ── Stage 7: Fraud Analysis (Phase 2C) ──────────────────────────────
        # Independent of Stages 5/6's success — fraud signals (claim
        # patterns, history, amount) don't require a computed payable
        # amount, so this always attempts to run if configured, even if
        # Policy/Financial degraded above.
        fraud_failed = False
        if self._fraud_analysis_agent is None:
            await tracer.skipped(
                TraceComponent.FRAUD_ANALYSIS, "Skipped — no fraud analysis agent configured for this pipeline"
            )
        else:
            fraud_result, fraud_failed = await self._run_soft_stage(
                tracer,
                TraceComponent.FRAUD_ANALYSIS,
                lambda: self._fraud_analysis_agent.run(claim),
                metadata_fn=lambda r: {
                    "flags": [f.code for f in r.flags],
                    "deterministic_thresholds_triggered": r.deterministic_thresholds_triggered,
                    "risk_level": r.risk_level.value,
                    "same_day_claim_count": r.same_day_claim_count,
                    "monthly_claim_count": r.monthly_claim_count,
                    "requires_manual_review": r.requires_manual_review,
                },
                confidence_fn=lambda r: r.confidence,
            )
            claim.fraud_analysis_result = fraud_result

        # ── End of Phase 2C ───────────────────────────────────────────────────
        await tracer.completed(
            TraceComponent.PIPELINE,
            message="Reached end of Phase 2C pipeline — final decision generation is not yet implemented",
            duration_ms=(time.monotonic() - t0) * 1000,
            metadata={
                "policy_failed": policy_failed,
                "financial_failed": financial_failed,
                "fraud_failed": fraud_failed,
            },
        )
        claim.status = ClaimStatus.PROCESSING
        claim.stopped_at = None
        claim.user_message = _final_user_message(
            claim, policy_failed=policy_failed, financial_failed=financial_failed, fraud_failed=fraud_failed
        )
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

    async def _run_soft_stage(
        self,
        tracer: TraceService,
        component: TraceComponent,
        coro_factory: Callable[[], Coroutine[Any, Any, T]],
        *,
        metadata_fn: Callable[[T], Dict[str, Any]],
        confidence_fn: Optional[Callable[[T], Optional[float]]] = None,
    ) -> Tuple[Optional[T], bool]:
        """
        Like `_run_stage`, but a failure does NOT propagate — used for
        Phase 2C's Policy/Financial/Fraud stages, which are findings for
        Phase 2D to weigh, not early-stop gates (see module docstring
        "Phase 2C note"). Returns `(result, failed)`: on success,
        `(result, False)`; on a genuine exception, `(None, True)` — the
        FAILED trace event is still recorded (same safe error info as
        `_run_stage`), but the caller continues to the next stage instead
        of degrading the whole claim. The corresponding `claim.*_result`
        field is left `None` by the caller — never a guessed/fabricated
        result.
        """
        t0 = time.monotonic()
        await tracer.started(component)
        try:
            result = await coro_factory()
        except Exception as exc:
            await tracer.failed(component, exc, duration_ms=(time.monotonic() - t0) * 1000)
            return None, True
        else:
            await tracer.completed(
                component,
                duration_ms=(time.monotonic() - t0) * 1000,
                metadata=metadata_fn(result),
                confidence=confidence_fn(result) if confidence_fn else None,
            )
            return result, False
