"""
FraudAnalysisAgent — Phase 2C, pipeline stage 7.

Answers "are there fraud/manual-review signals for this claim?" — using
deterministic thresholds from policy_terms.json's fraud_thresholds
(PolicyRepository), which remain authoritative. Never itself decides
APPROVED/PARTIAL/REJECTED/MANUAL_REVIEW (DecisionGenerationAgent, Phase 2D)
— only flags signals for that later stage to weigh.

No AI call in this phase (ai_provider=None, like ClaimValidationAgent/
CrossDocumentValidationAgent/PolicyEngine) — the assignment brief allows an
AI-assisted signal as an *optional* addition, but every threshold this
phase needs (same-day/monthly claim counts, high-value, auto-manual-review)
is explicitly numeric and policy-defined, so a deterministic-only
implementation is both sufficient and the more conservative, more
explainable choice. See docs/AI_HANDOFF.md "Phase 2C" for the full
rationale on why this phase doesn't add a third AI-calling component.
If an AI-assisted signal is added later, `FraudAnalysisResult.ai_risk_score`
is the reserved, deliberately-separate field for it (see
app/domain/fraud.py) — it must never be merged into
`deterministic_thresholds_triggered`.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from app.agents.base_agent import BaseAgent
from app.domain.errors import ComponentFailureError
from app.domain.fraud import FraudAnalysisResult, FraudFlag, FraudRiskLevel
from app.domain.models import Claim, ClaimHistoryItem
from app.policy.policy_repository import FraudThresholds, PolicyRepository
from app.repositories.claim_repository import ClaimRepository


class FraudAnalysisAgent(BaseAgent):
    def __init__(
        self,
        *,
        policy_repository: PolicyRepository,
        claim_repository: Optional[ClaimRepository] = None,
        agent_name: Optional[str] = None,
    ) -> None:
        super().__init__(ai_provider=None, agent_name=agent_name)
        self._policy_repository = policy_repository
        self._claim_repository = claim_repository

    async def run(self, claim: Claim) -> FraudAnalysisResult:
        """
        Raises ComponentFailureError (recoverable=True) when
        `claim.submission.simulate_component_failure` is set — the
        assignment's own explicit resilience-testing hook
        (ClaimSubmission.simulate_component_failure has existed on the
        domain model since Phase 0, unused until now). ClaimsPipeline
        catches this and degrades gracefully (see its "soft-fail" handling
        for Phase 2C stages) rather than crashing the claim.
        """
        if claim.submission.simulate_component_failure:
            raise ComponentFailureError(
                "FraudAnalysisAgent", "simulated component failure (simulate_component_failure=True)"
            )

        thresholds = self._policy_repository.fraud_thresholds
        submission = claim.submission
        history = await self._resolve_history(claim)

        same_day_count = self._count_matching(history, submission.treatment_date, same_day=True) + 1
        monthly_count = self._count_matching(history, submission.treatment_date, same_day=False) + 1

        flags: List[FraudFlag] = []
        triggered: List[str] = []

        if same_day_count > thresholds.same_day_claims_limit:
            flags.append(FraudFlag(
                code="SAME_DAY_CLAIMS_LIMIT_EXCEEDED",
                message=(
                    f"{same_day_count} claim(s) for this member on {submission.treatment_date} "
                    f"exceeds same_day_claims_limit ({thresholds.same_day_claims_limit})."
                ),
                evidence={"same_day_claim_count": same_day_count, "limit": thresholds.same_day_claims_limit},
            ))
            triggered.append("SAME_DAY_CLAIMS_LIMIT_EXCEEDED")

        if monthly_count > thresholds.monthly_claims_limit:
            flags.append(FraudFlag(
                code="MONTHLY_CLAIMS_LIMIT_EXCEEDED",
                message=(
                    f"{monthly_count} claim(s) for this member in "
                    f"{submission.treatment_date.strftime('%Y-%m')} exceeds monthly_claims_limit "
                    f"({thresholds.monthly_claims_limit})."
                ),
                evidence={"monthly_claim_count": monthly_count, "limit": thresholds.monthly_claims_limit},
            ))
            triggered.append("MONTHLY_CLAIMS_LIMIT_EXCEEDED")

        is_high_value = submission.claimed_amount >= thresholds.high_value_claim_threshold
        if is_high_value:
            flags.append(FraudFlag(
                code="HIGH_VALUE_CLAIM",
                message=(
                    f"Claimed amount {submission.claimed_amount} meets/exceeds "
                    f"high_value_claim_threshold ({thresholds.high_value_claim_threshold})."
                ),
                evidence={"claimed_amount": str(submission.claimed_amount)},
            ))
            triggered.append("HIGH_VALUE_CLAIM")

        auto_manual_review = submission.claimed_amount > thresholds.auto_manual_review_above
        if auto_manual_review:
            flags.append(FraudFlag(
                code="AUTO_MANUAL_REVIEW_THRESHOLD_EXCEEDED",
                message=(
                    f"Claimed amount {submission.claimed_amount} exceeds auto_manual_review_above "
                    f"({thresholds.auto_manual_review_above})."
                ),
                evidence={"claimed_amount": str(submission.claimed_amount)},
            ))
            triggered.append("AUTO_MANUAL_REVIEW_THRESHOLD_EXCEEDED")

        # Manual review is warranted for a same-day/monthly pattern
        # breach or crossing auto_manual_review_above — NOT merely for
        # is_high_value alone (high-value is a softer, MEDIUM-risk signal;
        # see _compute_risk_level).
        requires_manual_review = (
            same_day_count > thresholds.same_day_claims_limit
            or monthly_count > thresholds.monthly_claims_limit
            or auto_manual_review
        )

        risk_level = self._compute_risk_level(requires_manual_review, is_high_value)

        return FraudAnalysisResult(
            risk_level=risk_level,
            flags=flags,
            deterministic_thresholds_triggered=triggered,
            same_day_claim_count=same_day_count,
            monthly_claim_count=monthly_count,
            is_high_value=is_high_value,
            requires_manual_review=requires_manual_review,
            ai_risk_score=None,
            confidence=1.0,
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _resolve_history(self, claim: Claim) -> List[ClaimHistoryItem]:
        """
        Two sources, one shape — same "fixture ground truth vs. real
        query" pattern DocumentInputAdapter established for Phase 2A
        (Decision 17): `submission.claims_history` (explicitly supplied,
        e.g. by the evaluation runner from test_cases.json — TC009's
        `claims_history` block) takes priority when present; otherwise the
        real persisted history is queried via ClaimRepository (the real
        API path). Never fabricates history — an empty result from either
        source just means "no prior claims found."
        """
        if claim.submission.claims_history:
            return claim.submission.claims_history
        if self._claim_repository is not None:
            return await self._claim_repository.list_by_member(
                claim.submission.member_id, exclude_claim_id=claim.claim_id
            )
        return []

    @staticmethod
    def _count_matching(history: List[ClaimHistoryItem], treatment_date: date, *, same_day: bool) -> int:
        if same_day:
            return sum(1 for h in history if h.date == treatment_date)
        return sum(1 for h in history if h.date.year == treatment_date.year and h.date.month == treatment_date.month)

    @staticmethod
    def _compute_risk_level(requires_manual_review: bool, is_high_value: bool) -> FraudRiskLevel:
        if requires_manual_review:
            return FraudRiskLevel.HIGH
        if is_high_value:
            return FraudRiskLevel.MEDIUM
        return FraudRiskLevel.LOW
