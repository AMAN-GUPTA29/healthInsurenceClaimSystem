"""
Unit tests for DecisionGenerationAgent — Phase 2D.

DecisionGenerationAgent makes no AI calls and does no I/O — every test
here builds a `Claim` with hand-constructed Phase 2A/2B/2C results
(never through the real pipeline) and asserts on the deterministic
decision produced. See docs/tradeoffs.md "Decision Precedence" for the
exact rule derivation these tests lock in.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

import pytest

from app.agents.decision_generation_agent import DecisionGenerationAgent
from app.domain.extraction import ClaimExtractionResult
from app.domain.fraud import FraudAnalysisResult, FraudFlag, FraudRiskLevel
from app.domain.models import (
    Claim,
    ClaimCategory,
    ClaimSubmission,
    DecisionType,
    DocumentMetadata,
    FinancialBreakdown,
    RejectionReason,
)
from app.domain.policy_evaluation import (
    LineItemPolicyFinding,
    PolicyEvaluationResult,
)
from app.domain.verification import CrossDocumentValidationResult, CrossDocumentValidationStatus, DocumentVerificationResult, DocumentVerificationStatus


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def agent() -> DecisionGenerationAgent:
    return DecisionGenerationAgent()


def _policy(
    *,
    covered: bool = True,
    exclusion_applies: bool = False,
    waiting_period_applies: bool = False,
    requires_pre_authorization: bool = False,
    pre_authorization_provided: bool = False,
    line_item_findings: Optional[List[LineItemPolicyFinding]] = None,
    confidence: float = 1.0,
    failed_rules: Optional[List[str]] = None,
) -> PolicyEvaluationResult:
    return PolicyEvaluationResult(
        covered=covered,
        coverage_category=ClaimCategory.CONSULTATION,
        findings=[],
        passed_rules=[],
        failed_rules=failed_rules or [],
        warnings=[],
        requires_pre_authorization=requires_pre_authorization,
        pre_authorization_provided=pre_authorization_provided,
        waiting_period_applies=waiting_period_applies,
        exclusion_applies=exclusion_applies,
        is_network_hospital=None,
        sub_limit=None,
        per_claim_limit=Decimal("5000"),
        annual_opd_limit=Decimal("50000"),
        copay_percent=10.0,
        network_discount_percent=0.0,
        line_item_findings=line_item_findings or [],
        confidence=confidence,
    )


def _financial(
    *,
    claimed_amount: Decimal = Decimal("1500"),
    eligible_amount: Optional[Decimal] = None,
    payable_amount: Decimal = Decimal("1350"),
    confidence: float = 1.0,
    warnings: Optional[List[str]] = None,
) -> FinancialBreakdown:
    eligible = eligible_amount if eligible_amount is not None else claimed_amount
    return FinancialBreakdown(
        claimed_amount=claimed_amount,
        eligible_amount=eligible,
        network_discount_percent=0.0,
        network_discount_amount=Decimal("0"),
        amount_after_network_discount=eligible,
        sub_limit=None,
        sub_limit_applied=False,
        per_claim_limit=Decimal("5000"),
        per_claim_limit_applied=False,
        annual_opd_limit=Decimal("50000"),
        annual_limit_applied=False,
        amount_after_limits=eligible,
        copay_percent=10.0,
        copay_amount=Decimal("0"),
        payable_amount=payable_amount,
        currency="INR",
        calculation_steps=["Claimed amount: " + str(claimed_amount)],
        warnings=warnings or [],
        confidence=confidence,
    )


def _fraud(
    *,
    risk_level: FraudRiskLevel = FraudRiskLevel.LOW,
    requires_manual_review: bool = False,
    flags: Optional[List[FraudFlag]] = None,
    triggered: Optional[List[str]] = None,
    confidence: float = 1.0,
) -> FraudAnalysisResult:
    return FraudAnalysisResult(
        risk_level=risk_level,
        flags=flags or [],
        deterministic_thresholds_triggered=triggered or [],
        same_day_claim_count=1,
        monthly_claim_count=1,
        is_high_value=False,
        requires_manual_review=requires_manual_review,
        ai_risk_score=None,
        confidence=confidence,
    )


def make_claim(
    *,
    claimed_amount: str = "1500",
    policy: Optional[PolicyEvaluationResult] = None,
    financial: Optional[FinancialBreakdown] = None,
    fraud: Optional[FraudAnalysisResult] = None,
    include_doc_results: bool = True,
    extraction_has_failures: bool = False,
) -> Claim:
    submission = ClaimSubmission(
        member_id="EMP001",
        policy_id="PLUM_GHI_2024",
        claim_category=ClaimCategory.CONSULTATION,
        treatment_date=date(2024, 11, 1),
        claimed_amount=Decimal(claimed_amount),
        documents=[DocumentMetadata(file_id="F1", file_name="rx.jpg")],
    )
    claim = Claim(submission=submission, created_at=datetime(2024, 11, 1))
    claim.policy_evaluation_result = policy
    claim.financial_calculation_result = financial
    claim.fraud_analysis_result = fraud
    if include_doc_results:
        claim.document_verification_result = DocumentVerificationResult(
            status=DocumentVerificationStatus.PASS,
            required_documents=[], received_documents=[], missing_documents=[], wrong_documents=[],
            quality_issues=[], classifications=[], user_message="", confidence=1.0,
        )
        claim.cross_document_validation_result = CrossDocumentValidationResult(
            status=CrossDocumentValidationStatus.PASS, patient_names={}, user_message="", confidence=1.0,
        )
    claim.extraction_result = ClaimExtractionResult(
        extractions=[], failures=[], skipped=[], confidence=1.0, has_failures=extraction_has_failures,
    )
    return claim


class TestCleanClaimApproved:
    @pytest.mark.anyio
    async def test_clean_claim_is_approved(self, agent):
        claim = make_claim(policy=_policy(), financial=_financial(), fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.APPROVED
        assert decision.approved_amount == Decimal("1350")
        assert decision.rejection_reasons == []
        assert decision.reason_code == "APPROVED_FULL"


class TestPartialLineItemExclusion:
    @pytest.mark.anyio
    async def test_partial_eligible_amount_is_partial(self, agent):
        """TC006-shaped: exclusion_applies=True but line_item_findings is
        non-empty (itemized dental bill) — must be PARTIAL, not REJECTED,
        since the exclusion is per-line-item, not claim-level."""
        line_items = [
            LineItemPolicyFinding(description="Root Canal", amount=Decimal("8000"), excluded=False),
            LineItemPolicyFinding(description="Teeth Whitening", amount=Decimal("4000"), excluded=True, reason="Cosmetic"),
        ]
        policy = _policy(exclusion_applies=True, line_item_findings=line_items)
        financial = _financial(claimed_amount=Decimal("12000"), eligible_amount=Decimal("8000"), payable_amount=Decimal("8000"))
        claim = make_claim(claimed_amount="12000", policy=policy, financial=financial, fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.PARTIAL
        assert decision.approved_amount == Decimal("8000")
        assert decision.reason_code == "PARTIAL_LINE_ITEM_EXCLUSION"
        assert decision.rejection_reasons == []
        assert len(decision.line_item_decisions) == 2
        covered = next(li for li in decision.line_item_decisions if li.description == "Root Canal")
        excluded = next(li for li in decision.line_item_decisions if li.description == "Teeth Whitening")
        assert covered.approved is True and covered.approved_amount == Decimal("8000")
        assert excluded.approved is False and excluded.approved_amount == Decimal("0")
        assert excluded.rejection_reason == RejectionReason.EXCLUDED_PROCEDURE


class TestExclusionRejected:
    @pytest.mark.anyio
    async def test_claim_level_exclusion_is_rejected(self, agent):
        """TC012-shaped: exclusion_applies=True, NO line_item_findings —
        a whole-claim exclusion, not itemized, so this must be REJECTED."""
        policy = _policy(exclusion_applies=True)
        claim = make_claim(policy=policy, financial=_financial(), fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.REJECTED
        assert decision.approved_amount == Decimal("0")
        assert RejectionReason.EXCLUDED_CONDITION in decision.rejection_reasons

    @pytest.mark.anyio
    async def test_exclusion_and_waiting_period_both_reported(self, agent):
        """TC012 also has a waiting-period finding alongside the exclusion —
        both reasons must be collected, not just the first one checked."""
        policy = _policy(exclusion_applies=True, waiting_period_applies=True)
        claim = make_claim(policy=policy, financial=_financial(), fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.REJECTED
        assert RejectionReason.EXCLUDED_CONDITION in decision.rejection_reasons
        assert RejectionReason.WAITING_PERIOD in decision.rejection_reasons


class TestWaitingPeriodRejected:
    @pytest.mark.anyio
    async def test_waiting_period_is_rejected(self, agent):
        policy = _policy(waiting_period_applies=True)
        claim = make_claim(policy=policy, financial=_financial(), fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.REJECTED
        assert decision.approved_amount == Decimal("0")
        assert decision.rejection_reasons == [RejectionReason.WAITING_PERIOD]
        assert decision.reason_code == "REJECTED_WAITING_PERIOD"


class TestPreAuthMissingRejected:
    @pytest.mark.anyio
    async def test_pre_auth_required_and_missing_is_rejected(self, agent):
        policy = _policy(requires_pre_authorization=True, pre_authorization_provided=False)
        claim = make_claim(policy=policy, financial=_financial(), fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.REJECTED
        assert decision.rejection_reasons == [RejectionReason.PRE_AUTH_MISSING]

    @pytest.mark.anyio
    async def test_pre_auth_required_and_provided_is_not_rejected(self, agent):
        policy = _policy(requires_pre_authorization=True, pre_authorization_provided=True)
        claim = make_claim(policy=policy, financial=_financial(), fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.APPROVED


class TestFraudManualReview:
    @pytest.mark.anyio
    async def test_fraud_requires_manual_review(self, agent):
        fraud = _fraud(
            risk_level=FraudRiskLevel.HIGH, requires_manual_review=True,
            triggered=["SAME_DAY_CLAIMS_LIMIT_EXCEEDED"],
        )
        claim = make_claim(policy=_policy(), financial=_financial(), fraud=fraud)
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.MANUAL_REVIEW
        assert decision.reason_code == "MANUAL_REVIEW_FRAUD"
        # A reliable financial calculation exists, so the amount is still surfaced.
        assert decision.approved_amount == Decimal("1350")
        assert "SAME_DAY_CLAIMS_LIMIT_EXCEEDED" in decision.fraud_signals

    @pytest.mark.anyio
    async def test_policy_rejection_takes_precedence_over_fraud(self, agent):
        """A claim that's both excluded AND fraud-flagged is REJECTED, not
        sent to manual review — there's nothing left to review."""
        policy = _policy(exclusion_applies=True)
        fraud = _fraud(risk_level=FraudRiskLevel.HIGH, requires_manual_review=True)
        claim = make_claim(policy=policy, financial=_financial(), fraud=fraud)
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.REJECTED


class TestPerClaimLimitExceededRejected:
    """
    Phase 3 correctness fix: the previous implementation applied
    sub_limit/per_claim_limit as FinancialCalculationService caps, which
    directly contradicted test_cases.json's own TC006/TC010 worked
    amounts. The corrected reading: per_claim_limit is a whole-claim
    REJECT gate, evaluated only when there is no line-item-driven partial
    eligibility to trust instead. See docs/tradeoffs.md "Decision
    Precedence" for the full derivation.
    """

    @pytest.mark.anyio
    async def test_claimed_amount_over_per_claim_limit_with_no_line_items_is_rejected(self, agent):
        """TC008-shaped: claimed 7500 > per_claim_limit 5000, no itemized
        line items (not DENTAL/VISION) — the whole claim is REJECTED, not
        capped to a partial payable amount."""
        policy = _policy()
        financial = _financial(claimed_amount=Decimal("7500"), eligible_amount=Decimal("7500"), payable_amount=Decimal("7500"))
        claim = make_claim(claimed_amount="7500", policy=policy, financial=financial, fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.REJECTED
        assert decision.approved_amount == Decimal("0")
        assert RejectionReason.PER_CLAIM_EXCEEDED in decision.rejection_reasons
        assert decision.reason_code == "REJECTED_PER_CLAIM_EXCEEDED"

    @pytest.mark.anyio
    async def test_claimed_amount_at_or_under_per_claim_limit_is_not_rejected(self, agent):
        """TC010-shaped: claimed 4500 <= per_claim_limit 5000 — no rejection,
        the claim proceeds to a normal APPROVED decision at the full
        (discount/copay-adjusted) payable amount."""
        policy = _policy()
        financial = _financial(claimed_amount=Decimal("4500"), eligible_amount=Decimal("4500"), payable_amount=Decimal("3240"))
        claim = make_claim(claimed_amount="4500", policy=policy, financial=financial, fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.APPROVED
        assert decision.approved_amount == Decimal("3240")
        assert decision.rejection_reasons == []

    @pytest.mark.anyio
    async def test_line_item_driven_partial_eligibility_is_trusted_over_per_claim_limit(self, agent):
        """TC006-shaped: claimed 12000 > per_claim_limit 5000, but line-item
        exclusion already establishes a lower, trusted eligible amount
        (8000) — the per-claim-limit gate must NOT fire; the claim is
        PARTIAL at the full line-item-eligible amount, not rejected and
        not further capped to 5000."""
        line_items = [
            LineItemPolicyFinding(description="Root Canal", amount=Decimal("8000"), excluded=False),
            LineItemPolicyFinding(description="Teeth Whitening", amount=Decimal("4000"), excluded=True, reason="Cosmetic"),
        ]
        policy = _policy(exclusion_applies=True, line_item_findings=line_items)
        financial = _financial(claimed_amount=Decimal("12000"), eligible_amount=Decimal("8000"), payable_amount=Decimal("8000"))
        claim = make_claim(claimed_amount="12000", policy=policy, financial=financial, fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.PARTIAL
        assert decision.approved_amount == Decimal("8000")
        assert RejectionReason.PER_CLAIM_EXCEEDED not in decision.rejection_reasons

    @pytest.mark.anyio
    async def test_claim_level_exclusion_reported_alone_even_when_per_claim_limit_also_exceeded(self, agent):
        """TC012-shaped: claim-level exclusion AND claimed amount (8000)
        exceeds per_claim_limit (5000) — only EXCLUDED_CONDITION is
        reported, matching test_cases.json's own single-reason expectation.
        The per-claim-limit gate is only evaluated when no other rejection
        reason already applies."""
        policy = _policy(exclusion_applies=True)
        financial = _financial(claimed_amount=Decimal("8000"), eligible_amount=Decimal("8000"), payable_amount=Decimal("8000"))
        claim = make_claim(claimed_amount="8000", policy=policy, financial=financial, fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.REJECTED
        assert decision.rejection_reasons == [RejectionReason.EXCLUDED_CONDITION]


class TestZeroPayableRejected:
    @pytest.mark.anyio
    async def test_zero_payable_is_rejected(self, agent):
        financial = _financial(claimed_amount=Decimal("100"), eligible_amount=Decimal("100"), payable_amount=Decimal("0"))
        claim = make_claim(claimed_amount="100", policy=_policy(), financial=financial, fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.REJECTED
        assert decision.approved_amount == Decimal("0")
        assert decision.reason_code == "REJECTED_ZERO_PAYABLE"


class TestDegradedComponentManualReview:
    @pytest.mark.anyio
    async def test_missing_policy_and_financial_is_manual_review_insufficient_evidence(self, agent):
        claim = make_claim(policy=None, financial=None, fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.MANUAL_REVIEW
        assert decision.reason_code == "MANUAL_REVIEW_INSUFFICIENT_EVIDENCE"
        assert decision.approved_amount is None
        assert "POLICY_ENGINE" in decision.degraded_components
        assert "FINANCIAL_CALCULATION" in decision.degraded_components
        assert decision.has_component_failures is True

    @pytest.mark.anyio
    async def test_missing_fraud_alone_does_not_force_manual_review(self, agent):
        """TC011-shaped: FraudAnalysisAgent failed/skipped, but Policy and
        Financial are both clean — must still be able to reach APPROVED,
        just with reduced confidence and manual_review_recommended=True."""
        claim = make_claim(policy=_policy(), financial=_financial(), fraud=None)
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.APPROVED
        assert decision.confidence_score < 1.0
        assert "FRAUD_ANALYSIS" in decision.degraded_components
        assert decision.manual_review_recommended is True

    @pytest.mark.anyio
    async def test_low_confidence_downgrades_to_manual_review_but_keeps_amount(self, agent):
        """Multiple degraded/low-confidence signals push overall confidence
        below the safe-auto-decision threshold — MANUAL_REVIEW, but the
        already-reliable financial figure is still surfaced (per spec: use
        the existing financial result if a reliable calculation exists)."""
        policy = _policy(confidence=0.5)
        financial = _financial(confidence=0.5)
        claim = make_claim(policy=policy, financial=financial, fraud=None, extraction_has_failures=True)
        decision = await agent.run(claim)
        assert decision.decision == DecisionType.MANUAL_REVIEW
        assert decision.reason_code == "MANUAL_REVIEW_LOW_CONFIDENCE"
        assert decision.approved_amount == Decimal("1350")


class TestDeterministicAmountPreserved:
    @pytest.mark.anyio
    async def test_approved_amount_always_equals_financial_payable_amount(self, agent):
        """No matter the decision path, approved_amount is never anything
        other than exactly FinancialCalculationService's own payable_amount
        (or None/0 when there is no reliable figure) — DecisionGenerationAgent
        never independently computes or adjusts a number."""
        financial = _financial(payable_amount=Decimal("1234.56"))
        claim = make_claim(policy=_policy(), financial=financial, fraud=_fraud())
        decision = await agent.run(claim)
        assert decision.approved_amount == financial.payable_amount

    @pytest.mark.anyio
    async def test_agent_has_no_ai_provider_and_never_calls_one(self, agent):
        """DecisionGenerationAgent is deterministic by construction — no AI
        provider is ever wired in, so there is no code path through which
        an LLM could influence or override the decision or the amount."""
        assert agent.has_ai_provider is False
