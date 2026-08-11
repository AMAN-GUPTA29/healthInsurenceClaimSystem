"""
Unit tests for ClaimValidationAgent.

Runs against the real PolicyRepository (real policy_terms.json) — the
member roster, policy ID, and minimum claim amount checked here are real
data, not fixtures invented for the test.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.agents.claim_validation_agent import ClaimValidationAgent
from app.domain.models import ClaimCategory, ClaimSubmission, DocumentMetadata
from app.policy.policy_repository import PolicyRepository


@pytest.fixture
def agent() -> ClaimValidationAgent:
    return ClaimValidationAgent(policy_repository=PolicyRepository())


def make_submission(**overrides) -> ClaimSubmission:
    defaults = dict(
        member_id="EMP001",
        policy_id="PLUM_GHI_2024",
        claim_category=ClaimCategory.CONSULTATION,
        treatment_date=date(2024, 11, 1),
        claimed_amount=Decimal("1500"),
        documents=[DocumentMetadata(file_id="F001", file_name="rx.jpg")],
    )
    defaults.update(overrides)
    return ClaimSubmission(**defaults)


class TestValidSubmission:
    @pytest.mark.anyio
    async def test_valid_submission_passes(self, agent):
        result = await agent.run(make_submission())
        assert result.valid is True
        assert result.errors == []


class TestMemberValidation:
    @pytest.mark.anyio
    async def test_unknown_member_fails(self, agent):
        result = await agent.run(make_submission(member_id="EMP999"))
        assert result.valid is False
        assert any(e.code == "MEMBER_NOT_FOUND" for e in result.errors)

    @pytest.mark.anyio
    async def test_dependent_member_is_valid(self, agent):
        result = await agent.run(make_submission(member_id="DEP001"))
        assert result.valid is True


class TestPolicyValidation:
    @pytest.mark.anyio
    async def test_wrong_policy_id_fails(self, agent):
        result = await agent.run(make_submission(policy_id="WRONG_POLICY_ID"))
        assert result.valid is False
        assert any(e.code == "POLICY_NOT_FOUND" for e in result.errors)


class TestCategoryValidation:
    @pytest.mark.anyio
    async def test_all_declared_categories_are_valid(self, agent):
        for category in ClaimCategory:
            result = await agent.run(make_submission(claim_category=category))
            assert not any(e.code == "UNSUPPORTED_CATEGORY" for e in result.errors)


class TestTreatmentDateValidation:
    @pytest.mark.anyio
    async def test_past_treatment_date_is_valid(self, agent):
        result = await agent.run(make_submission(treatment_date=date(2020, 1, 1)))
        assert result.valid is True

    def test_future_treatment_date_rejected_at_model_level(self):
        # Enforced by ClaimSubmission's own Pydantic validator (Phase 0),
        # not re-implemented here.
        with pytest.raises(Exception):
            make_submission(treatment_date=date(2099, 1, 1))


class TestClaimedAmountValidation:
    @pytest.mark.anyio
    async def test_amount_at_minimum_passes(self, agent):
        result = await agent.run(make_submission(claimed_amount=Decimal("500")))
        assert result.valid is True

    @pytest.mark.anyio
    async def test_amount_below_minimum_fails(self, agent):
        result = await agent.run(make_submission(claimed_amount=Decimal("499")))
        assert result.valid is False
        assert any(e.code == "BELOW_MINIMUM_AMOUNT" for e in result.errors)

    @pytest.mark.anyio
    async def test_below_minimum_error_names_the_amounts(self, agent):
        result = await agent.run(make_submission(claimed_amount=Decimal("100")))
        error = next(e for e in result.errors if e.code == "BELOW_MINIMUM_AMOUNT")
        assert "100" in error.message
        assert "500" in error.message


class TestMultipleErrors:
    @pytest.mark.anyio
    async def test_multiple_failures_are_all_reported(self, agent):
        result = await agent.run(
            make_submission(member_id="EMP999", claimed_amount=Decimal("10"))
        )
        codes = {e.code for e in result.errors}
        assert "MEMBER_NOT_FOUND" in codes
        assert "BELOW_MINIMUM_AMOUNT" in codes


@pytest.fixture
def anyio_backend():
    return "asyncio"
