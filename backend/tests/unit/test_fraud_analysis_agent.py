"""
Unit tests for FraudAnalysisAgent — Phase 2C.

Thresholds are read from the real policy_terms.json via PolicyRepository
(never copied into independent test constants). History is supplied via
`submission.claims_history` (the fixture path — see agent docstring for
the "one shape, two sources" pattern) so these tests never touch the
database.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.agents.fraud_analysis_agent import FraudAnalysisAgent
from app.domain.errors import ComponentFailureError
from app.domain.fraud import FraudRiskLevel
from app.domain.models import Claim, ClaimCategory, ClaimHistoryItem, ClaimSubmission, DocumentMetadata
from app.policy.policy_repository import PolicyRepository


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def policy_repository() -> PolicyRepository:
    return PolicyRepository()


@pytest.fixture
def agent(policy_repository) -> FraudAnalysisAgent:
    return FraudAnalysisAgent(policy_repository=policy_repository)


def make_claim(
    *, amount: str, treatment_date: date = date(2024, 10, 30), history=None, simulate_failure: bool = False,
) -> Claim:
    submission = ClaimSubmission(
        member_id="EMP008", policy_id="PLUM_GHI_2024", claim_category=ClaimCategory.CONSULTATION,
        treatment_date=treatment_date, claimed_amount=Decimal(amount),
        documents=[DocumentMetadata(file_id="F1", file_name="rx.jpg")],
        claims_history=history or [], simulate_component_failure=simulate_failure,
    )
    return Claim(submission=submission)


class TestHighValueThreshold:
    @pytest.mark.anyio
    async def test_below_high_value_threshold(self, agent, policy_repository):
        below = policy_repository.fraud_thresholds.high_value_claim_threshold - Decimal("1")
        claim = make_claim(amount=str(below))
        result = await agent.run(claim)
        assert result.is_high_value is False
        assert "HIGH_VALUE_CLAIM" not in result.deterministic_thresholds_triggered

    @pytest.mark.anyio
    async def test_at_or_above_high_value_threshold(self, agent, policy_repository):
        at = policy_repository.fraud_thresholds.high_value_claim_threshold
        claim = make_claim(amount=str(at))
        result = await agent.run(claim)
        assert result.is_high_value is True
        assert "HIGH_VALUE_CLAIM" in result.deterministic_thresholds_triggered
        assert result.risk_level == FraudRiskLevel.MEDIUM


class TestAutoManualReviewThreshold:
    @pytest.mark.anyio
    async def test_above_auto_manual_review_threshold_forces_manual_review(self, agent, policy_repository):
        above = policy_repository.fraud_thresholds.auto_manual_review_above + Decimal("1")
        claim = make_claim(amount=str(above))
        result = await agent.run(claim)
        assert result.requires_manual_review is True
        assert result.risk_level == FraudRiskLevel.HIGH
        assert "AUTO_MANUAL_REVIEW_THRESHOLD_EXCEEDED" in result.deterministic_thresholds_triggered

    @pytest.mark.anyio
    async def test_at_threshold_does_not_trigger_strictly_greater_than(self, agent, policy_repository):
        at = policy_repository.fraud_thresholds.auto_manual_review_above
        claim = make_claim(amount=str(at))
        result = await agent.run(claim)
        assert "AUTO_MANUAL_REVIEW_THRESHOLD_EXCEEDED" not in result.deterministic_thresholds_triggered


class TestSameDayClaimsThreshold:
    @pytest.mark.anyio
    async def test_same_day_limit_exceeded_triggers_manual_review(self, agent, policy_repository):
        """TC009-shaped: 3 prior same-day claims + this one = 4, limit=2."""
        limit = policy_repository.fraud_thresholds.same_day_claims_limit
        treatment_date = date(2024, 10, 30)
        history = [
            ClaimHistoryItem(claim_id=f"CLM_{i}", date=treatment_date, amount=Decimal("1000"), provider="Clinic")
            for i in range(limit + 1)
        ]
        claim = make_claim(amount="4800", treatment_date=treatment_date, history=history)
        result = await agent.run(claim)
        assert result.same_day_claim_count == limit + 2  # history + current
        assert result.requires_manual_review is True
        assert "SAME_DAY_CLAIMS_LIMIT_EXCEEDED" in result.deterministic_thresholds_triggered
        assert result.risk_level == FraudRiskLevel.HIGH

    @pytest.mark.anyio
    async def test_within_same_day_limit_does_not_trigger(self, agent, policy_repository):
        treatment_date = date(2024, 10, 30)
        history = [
            ClaimHistoryItem(claim_id="CLM_1", date=treatment_date, amount=Decimal("1000"), provider="Clinic"),
        ]
        claim = make_claim(amount="1000", treatment_date=treatment_date, history=history)
        result = await agent.run(claim)
        assert result.same_day_claim_count == 2  # within same_day_claims_limit=2
        assert "SAME_DAY_CLAIMS_LIMIT_EXCEEDED" not in result.deterministic_thresholds_triggered

    @pytest.mark.anyio
    async def test_current_claim_is_counted_exactly_once(self, agent):
        """Documented assumption (agent docstring): the current claim is
        always included in same_day_claim_count — a claim with zero prior
        history still counts as 1, not 0."""
        claim = make_claim(amount="1000", history=[])
        result = await agent.run(claim)
        assert result.same_day_claim_count == 1


class TestMonthlyClaimsThreshold:
    @pytest.mark.anyio
    async def test_monthly_limit_exceeded_triggers_manual_review(self, agent, policy_repository):
        limit = policy_repository.fraud_thresholds.monthly_claims_limit
        treatment_date = date(2024, 10, 30)
        # Spread across different days within October so same-day limit isn't also tripped.
        history = [
            ClaimHistoryItem(claim_id=f"CLM_{i}", date=date(2024, 10, i + 1), amount=Decimal("500"), provider="Clinic")
            for i in range(limit)
        ]
        claim = make_claim(amount="1000", treatment_date=treatment_date, history=history)
        result = await agent.run(claim)
        assert result.monthly_claim_count == limit + 1
        assert result.requires_manual_review is True
        assert "MONTHLY_CLAIMS_LIMIT_EXCEEDED" in result.deterministic_thresholds_triggered

    @pytest.mark.anyio
    async def test_claims_in_a_different_month_are_not_counted(self, agent):
        history = [
            ClaimHistoryItem(claim_id="CLM_1", date=date(2024, 9, 15), amount=Decimal("500"), provider="Clinic"),
        ]
        claim = make_claim(amount="1000", treatment_date=date(2024, 10, 30), history=history)
        result = await agent.run(claim)
        assert result.monthly_claim_count == 1  # only the current claim


class TestHistoricalClaims:
    @pytest.mark.anyio
    async def test_no_historical_claims_is_low_risk(self, agent):
        claim = make_claim(amount="1000", history=[])
        result = await agent.run(claim)
        assert result.risk_level == FraudRiskLevel.LOW
        assert result.flags == []

    @pytest.mark.anyio
    async def test_multiple_historical_claims_all_counted(self, agent):
        treatment_date = date(2024, 10, 30)
        history = [
            ClaimHistoryItem(claim_id="A", date=treatment_date, amount=Decimal("100"), provider="X"),
            ClaimHistoryItem(claim_id="B", date=date(2024, 10, 5), amount=Decimal("100"), provider="Y"),
            ClaimHistoryItem(claim_id="C", date=date(2024, 9, 1), amount=Decimal("100"), provider="Z"),
        ]
        claim = make_claim(amount="1000", treatment_date=treatment_date, history=history)
        result = await agent.run(claim)
        assert result.same_day_claim_count == 2  # A + current
        assert result.monthly_claim_count == 3  # A + B + current (C is September)

    @pytest.mark.anyio
    async def test_history_never_fabricated_when_repository_absent(self, policy_repository):
        """No claim_repository injected and no submission.claims_history —
        must return empty history, never invent claims."""
        agent_without_repo = FraudAnalysisAgent(policy_repository=policy_repository, claim_repository=None)
        claim = make_claim(amount="1000", history=[])
        result = await agent_without_repo.run(claim)
        assert result.same_day_claim_count == 1
        assert result.monthly_claim_count == 1


class TestDeterministicVsAiSeparation:
    @pytest.mark.anyio
    async def test_ai_risk_score_is_none_in_deterministic_only_phase(self, agent):
        claim = make_claim(amount="1000")
        result = await agent.run(claim)
        assert result.ai_risk_score is None
        # Deterministic triggers must never be conflated with an AI score.
        assert isinstance(result.deterministic_thresholds_triggered, list)


class TestFraudAnalysisFailure:
    @pytest.mark.anyio
    async def test_simulate_component_failure_raises_recoverable_error(self, agent):
        claim = make_claim(amount="1000", simulate_failure=True)
        with pytest.raises(ComponentFailureError) as exc_info:
            await agent.run(claim)
        assert exc_info.value.recoverable is True
