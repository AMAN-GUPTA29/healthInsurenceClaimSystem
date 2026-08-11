"""
DecisionGenerationAgent — Phase 2D, pipeline stage 8.

The DETERMINISTIC authority on the final claim decision
(APPROVED/PARTIAL/REJECTED/MANUAL_REVIEW). Makes no AI calls
(ai_provider=None, same pattern as ClaimValidationAgent/PolicyEngine/
FraudAnalysisAgent) and performs NO independent policy or financial
calculation of its own — it only combines the already-computed outputs of
PolicyEngine, FinancialCalculationService, and FraudAnalysisAgent (Phase
2C) plus DocumentExtractionAgent (Phase 2B). See docs/architecture.md
"Decision Generation (Phase 2D)" for the full rationale and
docs/tradeoffs.md "Decision Precedence" for exactly how each precedence
rule was derived from assignment.md/test_cases.json/policy_terms.json —
including the per-claim-limit reject gate (Rule 5.5, added in the Phase 3
correctness pass) that reconciles TC006/TC008/TC010 without any
test-case-specific branching.

Input/output: `run(claim: Claim) -> ClaimDecision`. No separate
`DecisionGenerationInput` wrapper model — `Claim` already aggregates every
result this agent needs (validation/document-verification/cross-document-
validation/extraction/policy/financial/fraud), exactly the same pattern
`PolicyEngine.evaluate(claim)` and `FraudAnalysisAgent.run(claim)` already
use. Introducing a parallel DTO here would just duplicate `Claim`'s own
fields for no benefit.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from app.agents.base_agent import BaseAgent
from app.domain.models import (
    Claim,
    ClaimDecision,
    DecisionType,
    LineItemDecision,
    RejectionReason,
)

# Deterministic confidence penalty per degraded/missing Phase 2C+ component
# beyond simply excluding it from the min()-of-available-scores baseline —
# see docs/tradeoffs.md "Decision Confidence Strategy" for why a MISSING
# result is treated as strictly worse than a merely-low-confidence one.
_DEGRADED_PENALTY = 0.15

# Below this, the pipeline has *some* evidence but not enough for a safe
# automatic APPROVED/PARTIAL — downgrade to MANUAL_REVIEW instead (Rule 10).
# Calibrated so a single degraded non-critical component (e.g. Fraud
# Analysis alone, as in TC011) never crosses it on its own — see
# docs/tradeoffs.md for the worked numbers.
_LOW_CONFIDENCE_THRESHOLD = 0.5


class DecisionGenerationAgent(BaseAgent):
    """Deterministic final-decision combiner. See module docstring."""

    def __init__(self, *, agent_name: Optional[str] = None) -> None:
        super().__init__(ai_provider=None, agent_name=agent_name)

    async def run(self, claim: Claim) -> ClaimDecision:
        submission = claim.submission
        policy = claim.policy_evaluation_result
        financial = claim.financial_calculation_result
        fraud = claim.fraud_analysis_result

        confidence, degraded = self._compute_confidence(claim)

        base_kwargs = dict(
            claim_id=claim.claim_id,
            member_id=submission.member_id,
            policy_id=submission.policy_id,
            category=submission.claim_category,
            treatment_date=submission.treatment_date,
            claimed_amount=submission.claimed_amount,
            financial_breakdown=financial,
            degraded_components=degraded,
            manual_review_recommended=bool(degraded),
            has_component_failures=bool(degraded),
            fraud_signals=list(fraud.deterministic_thresholds_triggered) if fraud else [],
        )

        # ── Rule 1/10: insufficient evidence for any safe automatic decision ──
        # Financial Calculation can never run without a Policy result (the
        # pipeline itself enforces this — see ClaimsPipeline Stage 6), so in
        # practice this fires only when Policy Evaluation itself failed or
        # was never configured.
        if policy is None or financial is None:
            return ClaimDecision(
                **base_kwargs,
                decision=DecisionType.MANUAL_REVIEW,
                approved_amount=None,
                reason_code="MANUAL_REVIEW_INSUFFICIENT_EVIDENCE",
                confidence_score=min(confidence, 0.3),
                explanation=(
                    "Policy evaluation and/or financial calculation could not be completed for this "
                    "claim, so an automatic decision cannot be made safely. This claim requires manual "
                    "review by a team member."
                ),
                member_facing_message=(
                    "We weren't able to fully process your claim automatically. A team member will "
                    "review it and get back to you."
                ),
            )

        # ── Rules 3-5: hard policy rejections ──────────────────────────────
        # Collected together (not first-match-wins) so a claim with multiple
        # simultaneous policy problems (e.g. an excluded condition that is
        # also still within its waiting period) reports every applicable
        # reason, not just the first one checked.
        rejection_reasons: List[RejectionReason] = []
        rejection_notes: List[str] = []

        # Rule 3: explicit, CLAIM-LEVEL policy exclusion only. A DENTAL/
        # VISION claim with `line_item_findings` (itemized bill) can also
        # have `exclusion_applies=True` purely because one line item was
        # excluded (e.g. cosmetic teeth whitening) — that is a PARTIAL
        # approval (root canal still payable), not a full rejection. See
        # docs/tradeoffs.md "Decision Precedence" for why this distinction
        # matters and how it was derived from TC006 vs TC012.
        if policy.exclusion_applies and not policy.line_item_findings:
            rejection_reasons.append(RejectionReason.EXCLUDED_CONDITION)
            rejection_notes.append("An excluded condition/treatment applies to this claim.")

        # Rule 4: waiting period
        if policy.waiting_period_applies:
            rejection_reasons.append(RejectionReason.WAITING_PERIOD)
            rejection_notes.append("A policy waiting period applies and has not yet elapsed.")

        # Rule 5: required pre-authorization missing
        if policy.requires_pre_authorization and not policy.pre_authorization_provided:
            rejection_reasons.append(RejectionReason.PRE_AUTH_MISSING)
            rejection_notes.append("Pre-authorization was required for this claim but was not obtained.")

        # Rule 5.5: whole-claim per-claim-limit breach. `eligible_amount`
        # equals `claimed_amount` unless a genuine line-item exclusion
        # (DENTAL/VISION) has already reduced it — computed once here and
        # reused below for the PARTIAL/APPROVED split (Rules 8/9).
        #
        # Only fires when (a) no other rejection reason already applies —
        # this is the only reading consistent with TC012, whose claim-level
        # exclusion also happens to exceed per_claim_limit but is expected
        # to report EXCLUDED_CONDITION alone — and (b) the claim has no
        # line-item-driven partial eligibility to fall back on instead, so
        # a claim like TC006 (line items already establish a lower, trusted
        # eligible amount) is never re-gated by the raw claimed amount. This
        # is the only reading of policy_terms.json's global `per_claim_limit`
        # that reproduces test_cases.json's own official numbers for ALL of
        # TC004, TC006, TC008, and TC010 simultaneously — see
        # docs/tradeoffs.md "Decision Precedence" for the full derivation.
        is_partial = financial.eligible_amount < financial.claimed_amount
        if (
            not rejection_reasons
            and not is_partial
            and policy.per_claim_limit is not None
            and submission.claimed_amount > policy.per_claim_limit
        ):
            rejection_reasons.append(RejectionReason.PER_CLAIM_EXCEEDED)
            rejection_notes.append(
                f"The claimed amount ({submission.claimed_amount}) exceeds the policy's per-claim "
                f"limit ({policy.per_claim_limit})."
            )

        if rejection_reasons:
            return ClaimDecision(
                **base_kwargs,
                decision=DecisionType.REJECTED,
                approved_amount=Decimal("0"),
                rejection_reasons=rejection_reasons,
                reason_code="REJECTED_" + rejection_reasons[0].value,
                confidence_score=confidence,
                explanation=" ".join(rejection_notes),
                member_facing_message=" ".join(rejection_notes),
            )

        # ── Rule 6: fraud threshold requiring manual review ─────────────────
        # Checked only once no hard policy rejection already fired — an
        # excluded/waiting-period/pre-auth-missing claim is REJECTED
        # regardless of any fraud signal; there is nothing left to "review".
        if fraud is not None and fraud.requires_manual_review:
            return ClaimDecision(
                **base_kwargs,
                decision=DecisionType.MANUAL_REVIEW,
                approved_amount=financial.payable_amount,
                reason_code="MANUAL_REVIEW_FRAUD",
                confidence_score=min(confidence, 0.5),
                explanation=(
                    "This claim triggered deterministic fraud-review thresholds and requires manual "
                    "review before a final decision can be made. Financial calculation completed "
                    "normally and is shown for reference."
                ),
                member_facing_message=(
                    "Your claim needs a closer look from our team before we can finalise it. "
                    "We'll be in touch."
                ),
            )

        # ── Rule 7: nothing payable ──────────────────────────────────────────
        if financial.payable_amount <= 0:
            return ClaimDecision(
                **base_kwargs,
                decision=DecisionType.REJECTED,
                approved_amount=Decimal("0"),
                reason_code="REJECTED_ZERO_PAYABLE",
                confidence_score=confidence,
                explanation="Nothing is payable under the applicable policy terms for this claim.",
                member_facing_message="Unfortunately, nothing is payable for this claim under your policy terms.",
            )

        # ── Rules 8/9: partial vs. fully covered ────────────────────────────
        # `eligible_amount` is FinancialCalculationService's own "how much of
        # the CLAIMED amount is even eligible before discount/limits/copay"
        # figure (see FinancialBreakdown's docstring) — it is reduced below
        # claimed_amount ONLY by genuine line-item exclusion (DENTAL/VISION),
        # never by network discount or copay, both of which the assignment's
        # own TC010 treats as normal terms of a fully-APPROVED claim, not
        # partial ineligibility. `is_partial` was already computed above for
        # the per-claim-limit gate (Rule 5.5) — reused here, not recomputed.
        line_item_decisions = self._build_line_item_decisions(policy.line_item_findings)

        decision = DecisionType.PARTIAL if is_partial else DecisionType.APPROVED
        reason_code = "PARTIAL_LINE_ITEM_EXCLUSION" if is_partial else "APPROVED_FULL"
        explanation = (
            "Part of the claimed amount was excluded per policy terms; the remainder was approved."
            if is_partial
            else "The claim is covered under the applicable policy terms."
        )
        member_message = (
            "Some of what you claimed for isn't covered under your policy, so only part of it was "
            "approved."
            if is_partial
            else "Your claim has been approved."
        )

        # Rule 10 (confidence half): insufficient confidence for a safe
        # automatic APPROVED/PARTIAL, even though Policy+Financial are both
        # present and a reliable payable amount exists — downgrade to
        # MANUAL_REVIEW but keep the reliable amount (per the spec: "Use
        # the existing financial result if a reliable calculation exists").
        if confidence < _LOW_CONFIDENCE_THRESHOLD:
            decision = DecisionType.MANUAL_REVIEW
            reason_code = "MANUAL_REVIEW_LOW_CONFIDENCE"
            explanation = (
                f"{explanation} However, confidence in the available evidence is too low for an "
                "automatic decision, so this claim is routed for manual review."
            )
            member_message = (
                "Your claim needs a closer look from our team before we can finalise it. We'll be in touch."
            )

        return ClaimDecision(
            **base_kwargs,
            decision=decision,
            approved_amount=financial.payable_amount,
            line_item_decisions=line_item_decisions,
            reason_code=reason_code,
            confidence_score=confidence,
            explanation=explanation,
            member_facing_message=member_message,
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _build_line_item_decisions(line_item_findings) -> List[LineItemDecision]:
        """Maps PolicyEngine's per-line-item findings (DENTAL/VISION itemized
        bills only) onto the existing LineItemDecision model — never
        recomputes an amount, just relabels what PolicyEngine already found."""
        decisions: List[LineItemDecision] = []
        for finding in line_item_findings:
            amount = finding.amount if finding.amount is not None else Decimal("0")
            decisions.append(
                LineItemDecision(
                    description=finding.description,
                    claimed_amount=amount,
                    approved_amount=Decimal("0") if finding.excluded else amount,
                    approved=not finding.excluded,
                    rejection_reason=RejectionReason.EXCLUDED_PROCEDURE if finding.excluded else None,
                    notes=finding.reason,
                )
            )
        return decisions

    @staticmethod
    def _compute_confidence(claim: Claim) -> Tuple[float, List[str]]:
        """
        Deterministic, operational confidence score — NOT statistically
        calibrated (see docs/tradeoffs.md "Decision Confidence Strategy").
        Takes the minimum confidence across every stage that actually ran
        (the same "never fabricate, minimum wins" convention already used
        by ClaimExtractionResult/DocumentVerificationResult elsewhere in
        this codebase), then applies an additional penalty per stage that
        is MISSING entirely (failed, or never configured) — a missing
        result is treated as strictly worse than a merely-low-confidence
        one, since there is no evidence at all to fall back on.
        """
        scores: List[float] = []
        degraded: List[str] = []

        # DocumentVerificationResult/CrossDocumentValidationResult.confidence
        # are both Optional[float] (None when no AI call was actually made —
        # e.g. a pre-supplied fixture classification) — never fed into
        # min() unguarded, since comparing None to a float raises.
        if claim.document_verification_result is not None and claim.document_verification_result.confidence is not None:
            scores.append(claim.document_verification_result.confidence)
        if (
            claim.cross_document_validation_result is not None
            and claim.cross_document_validation_result.confidence is not None
        ):
            scores.append(claim.cross_document_validation_result.confidence)

        if claim.extraction_result is not None:
            if claim.extraction_result.confidence is not None:
                scores.append(claim.extraction_result.confidence)
            if claim.extraction_result.has_failures:
                degraded.append("DOCUMENT_EXTRACTION")
        else:
            degraded.append("DOCUMENT_EXTRACTION")

        if claim.policy_evaluation_result is not None:
            scores.append(claim.policy_evaluation_result.confidence)
        else:
            degraded.append("POLICY_ENGINE")

        if claim.financial_calculation_result is not None:
            scores.append(claim.financial_calculation_result.confidence)
        else:
            degraded.append("FINANCIAL_CALCULATION")

        if claim.fraud_analysis_result is not None:
            scores.append(claim.fraud_analysis_result.confidence)
        else:
            degraded.append("FRAUD_ANALYSIS")

        base = min(scores) if scores else 0.0
        confidence = max(0.0, base - _DEGRADED_PENALTY * len(degraded))
        return confidence, degraded
