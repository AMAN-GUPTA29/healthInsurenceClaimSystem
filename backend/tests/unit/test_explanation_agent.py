"""
Unit tests for ExplanationAgent — Phase 2D.

Uses a fake AIProvider double (no vendor SDK — same pattern as
test_document_verification_agent.py's _FakeAIProvider) so these tests
never touch the network. The real integration path (a genuine configured
provider) is exercised manually — see docs/AI_HANDOFF.md "Verification
(Phase 2D)".
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.agents.explanation_agent import ExplanationAgent
from app.ai.schemas.ai_schemas import AIStructuredResponse
from app.domain.explanation import ExplanationSource
from app.domain.models import (
    Claim,
    ClaimCategory,
    ClaimDecision,
    ClaimSubmission,
    DecisionType,
    DocumentMetadata,
    FinancialBreakdown,
    RejectionReason,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


VALID_AI_DATA = {
    "member_summary": "Your claim has been approved for Rs. 1350.",
    "operations_summary": "Consultation claim approved after 10% copay deduction.",
    "key_reasons": ["Fully covered under policy terms"],
    "deductions": ["10% co-pay: -Rs. 150.00"],
    "policy_findings": ["CONSULTATION_COVERED: PASSED"],
    "warnings": [],
    "next_action": "",
}


class _FakeAIProvider:
    """Minimal AIProvider double — only implements generate_structured,
    matching what ExplanationAgent actually calls."""

    def __init__(self, *, data: dict | None = None, exc: Exception | None = None):
        self._data = data
        self._exc = exc
        self.calls = 0

    async def generate_structured(self, request):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return AIStructuredResponse(data=self._data, model="fake-model", provider="fake", raw_text=None)

    async def analyze_document(self, request):  # pragma: no cover
        raise AssertionError("ExplanationAgent must never call analyze_document")


def make_claim() -> Claim:
    submission = ClaimSubmission(
        member_id="EMP001", policy_id="PLUM_GHI_2024", claim_category=ClaimCategory.CONSULTATION,
        treatment_date=date(2024, 11, 1), claimed_amount=Decimal("1500"),
        documents=[DocumentMetadata(file_id="F1", file_name="rx.jpg")],
    )
    return Claim(submission=submission, created_at=datetime(2024, 11, 1))


def make_decision(
    *, decision: DecisionType = DecisionType.APPROVED, approved_amount=Decimal("1350"),
    rejection_reasons=None, degraded_components=None, confidence_score: float = 1.0,
) -> ClaimDecision:
    return ClaimDecision(
        claim_id="CLM-TEST0001", member_id="EMP001", policy_id="PLUM_GHI_2024",
        category=ClaimCategory.CONSULTATION, treatment_date=date(2024, 11, 1),
        claimed_amount=Decimal("1500"), decision=decision, approved_amount=approved_amount,
        rejection_reasons=rejection_reasons or [], reason_code="APPROVED_FULL",
        confidence_score=confidence_score,
        explanation="The claim is covered under the applicable policy terms.",
        member_facing_message="Your claim has been approved.",
        degraded_components=degraded_components or [],
        financial_breakdown=FinancialBreakdown(
            claimed_amount=Decimal("1500"), eligible_amount=Decimal("1500"),
            network_discount_percent=0.0, network_discount_amount=Decimal("0"),
            amount_after_network_discount=Decimal("1500"), amount_after_limits=Decimal("1500"),
            copay_percent=10.0, copay_amount=Decimal("150"), payable_amount=Decimal("1350"),
            calculation_steps=["Claimed amount: 1500", "Co-pay (10%) deducted: -150 -> 1350"],
        ),
    )


class TestValidStructuredResponse:
    @pytest.mark.anyio
    async def test_valid_ai_response_is_used_as_is(self):
        provider = _FakeAIProvider(data=VALID_AI_DATA)
        agent = ExplanationAgent(ai_provider=provider)
        result = await agent.run(make_claim(), make_decision())
        assert result.source == ExplanationSource.AI
        assert result.degraded is False
        assert result.member_summary == VALID_AI_DATA["member_summary"]
        assert result.operations_summary == VALID_AI_DATA["operations_summary"]
        assert result.key_reasons == VALID_AI_DATA["key_reasons"]
        assert len(result.ai_calls) == 1
        assert result.ai_calls[0].provider == "fake"
        assert provider.calls == 1


class TestInvalidAIResponse:
    @pytest.mark.anyio
    async def test_malformed_response_falls_back(self):
        """Well-formed JSON, but missing required fields — must not crash,
        must fall back."""
        provider = _FakeAIProvider(data={"member_summary": "only this field"})
        agent = ExplanationAgent(ai_provider=provider)
        result = await agent.run(make_claim(), make_decision())
        assert result.source == ExplanationSource.FALLBACK
        assert result.degraded is True


class TestProviderTimeout:
    @pytest.mark.anyio
    async def test_timeout_falls_back(self):
        from app.domain.errors import AITimeoutError

        provider = _FakeAIProvider(exc=AITimeoutError("fake", 30))
        agent = ExplanationAgent(ai_provider=provider)
        result = await agent.run(make_claim(), make_decision())
        assert result.source == ExplanationSource.FALLBACK
        assert result.ai_calls == []


class TestProviderFailure:
    @pytest.mark.anyio
    async def test_generic_provider_error_falls_back(self):
        from app.domain.errors import AIProviderError

        provider = _FakeAIProvider(exc=AIProviderError("boom", provider="fake"))
        agent = ExplanationAgent(ai_provider=provider)
        result = await agent.run(make_claim(), make_decision())
        assert result.source == ExplanationSource.FALLBACK

    @pytest.mark.anyio
    async def test_unexpected_exception_still_falls_back_never_raises(self):
        """Deliberately broad catch (see ExplanationAgent's own docstring)
        — even a totally generic exception (simulating an SSL/network
        error the SDK doesn't wrap) must not propagate."""
        provider = _FakeAIProvider(exc=ConnectionResetError("connection reset"))
        agent = ExplanationAgent(ai_provider=provider)
        result = await agent.run(make_claim(), make_decision())
        assert result.source == ExplanationSource.FALLBACK

    @pytest.mark.anyio
    async def test_no_provider_configured_falls_back_without_attempting_a_call(self):
        agent = ExplanationAgent(ai_provider=None)
        result = await agent.run(make_claim(), make_decision())
        assert result.source == ExplanationSource.FALLBACK


class TestFallbackExplanationQuality:
    @pytest.mark.anyio
    async def test_fallback_uses_real_decision_fields_not_a_placeholder(self):
        decision = make_decision(
            decision=DecisionType.REJECTED, approved_amount=Decimal("0"),
            rejection_reasons=[RejectionReason.WAITING_PERIOD],
        )
        decision.explanation = "A policy waiting period applies and has not yet elapsed."
        decision.member_facing_message = "A policy waiting period applies and has not yet elapsed."
        agent = ExplanationAgent(ai_provider=_FakeAIProvider(exc=RuntimeError("boom")))
        result = await agent.run(make_claim(), decision)
        assert result.source == ExplanationSource.FALLBACK
        assert result.member_summary == decision.member_facing_message
        assert result.operations_summary == decision.explanation
        assert "WAITING_PERIOD" in result.key_reasons
        assert result.confidence <= 0.6

    @pytest.mark.anyio
    async def test_fallback_confidence_never_exceeds_decision_confidence(self):
        decision = make_decision(confidence_score=0.9)
        agent = ExplanationAgent(ai_provider=None)
        result = await agent.run(make_claim(), decision)
        assert result.confidence <= 0.9


class TestNoHallucinatedFacts:
    @pytest.mark.anyio
    async def test_evidence_never_includes_raw_document_or_storage_details(self):
        """The evidence sent to the LLM must be built entirely from
        already-verified decision/policy/financial/fraud fields — never
        storage_reference, raw extraction payloads, or anything not
        already computed deterministically."""
        agent = ExplanationAgent(ai_provider=_FakeAIProvider(data=VALID_AI_DATA))
        evidence = agent._build_evidence(make_claim(), make_decision())
        serialized = str(evidence)
        assert "storage_reference" not in serialized
        assert evidence["decision"] == "APPROVED"
        assert evidence["approved_amount"] == "1350"

    @pytest.mark.anyio
    async def test_fallback_never_claims_fraud_when_none_was_flagged(self):
        decision = make_decision()  # no fraud_signals set
        agent = ExplanationAgent(ai_provider=None)
        result = await agent.run(make_claim(), decision)
        assert not any("fraud" in r.lower() for r in result.key_reasons)
