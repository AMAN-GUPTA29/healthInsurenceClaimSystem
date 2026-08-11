"""
ExplanationAgent — Phase 2D, pipeline stage 9.

Turns an already-finalised `ClaimDecision` (produced deterministically by
DecisionGenerationAgent) plus its supporting evidence into a structured,
human-readable explanation, using the real configured `AIProvider` (never
a fixture/mock in the production path — see docs/AI_HANDOFF.md invariant
re: real AI). The LLM is explicitly instructed never to invent facts,
never to recalculate anything, and never to contradict the decision it is
explaining — see app/ai/prompts/explanation.py's system prompt for the
exact constraints, and docs/architecture.md "Explanation (Phase 2D)" for
why this split (deterministic decision, LLM-written explanation) is safe.

Failure handling is the whole point of this agent's design: if the AI call
fails or returns something that doesn't validate, this does NOT raise —
it returns a deterministic FALLBACK ExplanationResult built directly from
the same evidence the LLM would have seen, with `source=FALLBACK` and a
reduced `confidence`, so a caller can always tell the difference. The
`ClaimDecision` itself (already computed by DecisionGenerationAgent before
this agent ever runs) is never touched by this failure — see
ClaimsPipeline's Stage 9 integration for how a FAILED trace event and a
kept decision coexist.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.agents.base_agent import BaseAgent
from app.ai.prompts.explanation import build_explanation_request
from app.ai.providers.base import AIProvider
from app.domain.explanation import ExplanationAIResponse, ExplanationResult, ExplanationSource
from app.domain.models import Claim, ClaimDecision
from app.domain.trace import AITraceMetadata


class ExplanationAgent(BaseAgent):
    def __init__(self, *, ai_provider: Optional[AIProvider] = None, agent_name: Optional[str] = None) -> None:
        super().__init__(ai_provider=ai_provider, agent_name=agent_name)

    async def run(self, claim: Claim, decision: ClaimDecision) -> ExplanationResult:
        if not self.has_ai_provider:
            # No provider configured at all (e.g. an evaluation/test run
            # with no AI_PROVIDER set) — not a failure, just nothing to
            # call. Same "degrade, don't crash" outcome as an actual
            # provider error, distinguished only by not attempting a call.
            return self._fallback(decision)

        try:
            # Evidence-building is inside the same try as the AI call —
            # this agent's contract is "never raise", full stop, not just
            # "never raise past the AI call". An unexpected error reading
            # `claim`/`decision` fields is exactly as recoverable as an AI
            # provider error from this agent's point of view.
            evidence = self._build_evidence(claim, decision)
            request = build_explanation_request(evidence)
            response = await self.ai_provider.generate_structured(request)
            ai_response = ExplanationAIResponse.model_validate(response.data)
        except Exception as exc:  # noqa: BLE001 — deliberately broad, see below
            # Catches AIProviderError (timeout/rate-limit/auth/parse),
            # PydanticValidationError (well-formed JSON that doesn't match
            # ExplanationAIResponse), and anything else a real network/SSL/
            # surface as exception types this codebase doesn't wrap in
            # AIProviderError (see docs/AI_HANDOFF.md Known Issues re: the
            # corporate SSL proxy environment issue) — for THIS agent, the
            # non-negotiable requirement is that a failure here can never
            # take down a claim that already has a valid deterministic
            # decision (assignment.md point 6). Never disables TLS
            # verification or weakens the request itself to work around a
            # failure — it only decides how to report the failure.
            self._logger.warning(f"Explanation generation failed, using fallback: {exc}")
            return self._fallback(decision)
        else:
            return ExplanationResult(
                member_summary=ai_response.member_summary,
                operations_summary=ai_response.operations_summary,
                key_reasons=ai_response.key_reasons,
                deductions=ai_response.deductions,
                policy_findings=ai_response.policy_findings,
                warnings=ai_response.warnings,
                next_action=ai_response.next_action or None,
                source=ExplanationSource.AI,
                degraded=False,
                confidence=decision.confidence_score,
                ai_calls=[
                    AITraceMetadata(
                        provider=response.provider,
                        model=response.model,
                        latency_ms=response.latency_ms,
                        input_tokens=response.usage.input_tokens if response.usage else None,
                        output_tokens=response.usage.output_tokens if response.usage else None,
                    )
                ],
            )

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _fallback(decision: ClaimDecision) -> ExplanationResult:
        """
        Deterministic, evidence-grounded explanation used whenever the LLM
        call can't be trusted (no provider, provider error, or an invalid/
        unparseable response). Built entirely from fields already on
        `decision` (itself already-verified, deterministic output) — never
        a generic "something went wrong" placeholder, and never fabricates
        anything the LLM path wouldn't also have been given.
        """
        key_reasons: List[str] = list(decision.fraud_signals) if decision.fraud_signals else []
        if decision.rejection_reasons:
            key_reasons = [r.value for r in decision.rejection_reasons] + key_reasons

        return ExplanationResult(
            member_summary=decision.member_facing_message or "Your claim has been processed.",
            operations_summary=decision.explanation or "Claim processed; see decision fields for detail.",
            key_reasons=key_reasons,
            deductions=list(decision.financial_breakdown.calculation_steps) if decision.financial_breakdown else [],
            policy_findings=[],
            warnings=[
                f"Explanation generated via deterministic fallback — {c} degraded during processing."
                for c in decision.degraded_components
            ] or (["Explanation generated via deterministic fallback (AI call unavailable)."]),
            next_action=None,
            source=ExplanationSource.FALLBACK,
            degraded=True,
            confidence=min(decision.confidence_score, 0.6),
            ai_calls=[],
        )

    @staticmethod
    def _build_evidence(claim: Claim, decision: ClaimDecision) -> Dict[str, Any]:
        """
        The only thing the LLM ever sees — a compact, already-safe JSON
        summary. Deliberately excludes: raw documents/images, storage
        references, full extraction payloads, API keys, and internal trace
        ids. Every field here is something the member is already entitled
        to know about their own claim.
        """
        policy = claim.policy_evaluation_result
        financial = claim.financial_calculation_result
        fraud = claim.fraud_analysis_result
        extraction = claim.extraction_result

        return {
            "decision": decision.decision.value,
            "reason_code": decision.reason_code,
            "claimed_amount": str(decision.claimed_amount),
            "approved_amount": str(decision.approved_amount) if decision.approved_amount is not None else None,
            "rejection_reasons": [r.value for r in decision.rejection_reasons],
            "confidence_score": decision.confidence_score,
            "degraded_components": decision.degraded_components,
            "manual_review_recommended": decision.manual_review_recommended,
            "line_item_decisions": [
                {
                    "description": li.description,
                    "claimed_amount": str(li.claimed_amount),
                    "approved_amount": str(li.approved_amount),
                    "approved": li.approved,
                    "reason": li.notes,
                }
                for li in decision.line_item_decisions
            ],
            "policy": (
                {
                    "covered": policy.covered,
                    "failed_rules": policy.failed_rules,
                    "warnings": policy.warnings,
                    "waiting_period_applies": policy.waiting_period_applies,
                    "exclusion_applies": policy.exclusion_applies,
                    "requires_pre_authorization": policy.requires_pre_authorization,
                    "pre_authorization_provided": policy.pre_authorization_provided,
                    "is_network_hospital": policy.is_network_hospital,
                }
                if policy is not None
                else None
            ),
            "financial": (
                {
                    "claimed_amount": str(financial.claimed_amount),
                    "eligible_amount": str(financial.eligible_amount),
                    "network_discount_percent": financial.network_discount_percent,
                    "sub_limit_applied": financial.sub_limit_applied,
                    "per_claim_limit_applied": financial.per_claim_limit_applied,
                    "copay_percent": financial.copay_percent,
                    "payable_amount": str(financial.payable_amount),
                    "calculation_steps": financial.calculation_steps,
                    "warnings": financial.warnings,
                }
                if financial is not None
                else None
            ),
            "fraud": (
                {
                    "risk_level": fraud.risk_level.value,
                    "flags": [{"code": f.code, "message": f.message} for f in fraud.flags],
                    "requires_manual_review": fraud.requires_manual_review,
                }
                if fraud is not None
                else None
            ),
            "document_issues": (
                [f"{f.file_id}: {f.reason}" for f in extraction.failures] if extraction is not None else []
            ),
        }
