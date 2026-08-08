"""
Integration tests for ClaimsPipeline — the three agents wired together,
exercising real early-stop behavior and trace emission end-to-end (no
mocked agents). Uses the real PolicyRepository/policy_terms.json.
"""

from __future__ import annotations

import pytest

from app.agents.claim_validation_agent import ClaimValidationAgent
from app.agents.cross_document_validation_agent import CrossDocumentValidationAgent
from app.agents.document_verification_agent import DocumentVerificationAgent
from app.domain.errors import AITimeoutError
from app.domain.models import Claim, ClaimCategory, ClaimStatus, ClaimSubmission, DocumentMetadata
from app.domain.trace import TraceComponent, TraceContext, TraceEventType
from app.domain.verification import DocumentClassification
from app.pipeline.pipeline import ClaimsPipeline
from app.policy.policy_repository import PolicyRepository
from app.tracing.service import TraceService


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def policy_repository() -> PolicyRepository:
    return PolicyRepository()


def build_pipeline(policy_repository, ai_provider=None) -> ClaimsPipeline:
    return ClaimsPipeline(
        claim_validation_agent=ClaimValidationAgent(policy_repository=policy_repository),
        document_verification_agent=DocumentVerificationAgent(
            ai_provider=ai_provider, policy_repository=policy_repository
        ),
        cross_document_validation_agent=CrossDocumentValidationAgent(),
    )


def make_claim(**overrides) -> Claim:
    from datetime import date
    from decimal import Decimal

    defaults = dict(
        member_id="EMP001",
        policy_id="PLUM_GHI_2024",
        claim_category=ClaimCategory.CONSULTATION,
        treatment_date=date(2024, 11, 1),
        claimed_amount=Decimal("1500"),
        documents=[
            DocumentMetadata(file_id="F007", file_name="rx.jpg"),
            DocumentMetadata(file_id="F008", file_name="bill.jpg"),
        ],
    )
    defaults.update(overrides)
    return Claim(submission=ClaimSubmission(**defaults))


class TestFullPassThroughPhase2A:
    @pytest.mark.anyio
    async def test_claim_with_complete_correct_documents_reaches_end_of_phase_2a(self, policy_repository):
        claim = make_claim()
        classifications = {
            "F007": DocumentClassification(file_id="F007", document_type="PRESCRIPTION", confidence=1.0),
            "F008": DocumentClassification(file_id="F008", document_type="HOSPITAL_BILL", confidence=1.0),
        }
        pipeline = build_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.PROCESSING
        assert result.stopped_at is None
        assert result.decision is None  # Phase 2A never generates a decision
        assert result.document_verification_result.status.value == "PASS"
        assert result.cross_document_validation_result.status.value == "PASS"

    @pytest.mark.anyio
    async def test_full_pass_trace_has_no_failed_or_skipped_events(self, policy_repository):
        claim = make_claim()
        classifications = {
            "F007": DocumentClassification(file_id="F007", document_type="PRESCRIPTION", confidence=1.0),
            "F008": DocumentClassification(file_id="F008", document_type="HOSPITAL_BILL", confidence=1.0),
        }
        pipeline = build_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        await pipeline.run(claim, classifications=classifications, tracer=tracer)

        event_types = {e.event_type for e in tracer.events}
        assert TraceEventType.FAILED not in event_types
        assert TraceEventType.SKIPPED not in event_types
        assert tracer.events[-1].component == TraceComponent.PIPELINE
        assert tracer.events[-1].event_type == TraceEventType.COMPLETED


class TestEarlyStopAtClaimValidation:
    @pytest.mark.anyio
    async def test_unknown_member_stops_before_document_verification(self, policy_repository):
        claim = make_claim(member_id="EMP999")
        pipeline = build_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, tracer=tracer)

        assert result.status == ClaimStatus.BLOCKED
        assert result.stopped_at == "CLAIM_VALIDATION"
        assert result.document_verification_result is None
        assert result.cross_document_validation_result is None

        components_run = [e.component for e in tracer.events if e.event_type == TraceEventType.STARTED]
        assert TraceComponent.DOCUMENT_VERIFICATION not in components_run
        assert TraceComponent.CROSS_DOCUMENT_VALIDATION not in components_run

        skipped = [e.component for e in tracer.events if e.event_type == TraceEventType.SKIPPED]
        assert TraceComponent.DOCUMENT_VERIFICATION in skipped
        assert TraceComponent.CROSS_DOCUMENT_VALIDATION in skipped


class TestEarlyStopAtDocumentVerification:
    @pytest.mark.anyio
    async def test_missing_document_stops_before_cross_document_validation(self, policy_repository):
        claim = make_claim(documents=[DocumentMetadata(file_id="F1", file_name="rx.jpg")])
        classifications = {"F1": DocumentClassification(file_id="F1", document_type="PRESCRIPTION")}
        pipeline = build_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.BLOCKED
        assert result.stopped_at == "DOCUMENT_VERIFICATION"
        assert result.cross_document_validation_result is None

        skipped = [e.component for e in tracer.events if e.event_type == TraceEventType.SKIPPED]
        assert TraceComponent.CROSS_DOCUMENT_VALIDATION in skipped


class TestGracefulDegradationOnAIFailure:
    """Section 24/45 requirement: an AI provider failure must not crash
    the pipeline, must be recorded in the trace, and must not be silently
    treated as a pass."""

    @pytest.mark.anyio
    async def test_ai_timeout_degrades_instead_of_raising(self, policy_repository):
        class _FailingAIProvider:
            async def generate_structured(self, request):
                raise AITimeoutError("gemini", 60)

        claim = make_claim(documents=[DocumentMetadata(file_id="F1", file_name="rx.jpg")])
        # No pre-supplied classification for F1 -> forces the AI path, which fails.
        pipeline = build_pipeline(policy_repository, ai_provider=_FailingAIProvider())
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, tracer=tracer)  # must not raise

        assert result.status == ClaimStatus.BLOCKED
        assert result.stopped_at == "DOCUMENT_VERIFICATION"
        assert result.document_verification_result is None  # never populated — not silently "passed"
        assert "technical problem" in result.user_message.lower()

    @pytest.mark.anyio
    async def test_ai_failure_recorded_as_failed_in_trace(self, policy_repository):
        class _FailingAIProvider:
            async def generate_structured(self, request):
                raise AITimeoutError("gemini", 60)

        claim = make_claim(documents=[DocumentMetadata(file_id="F1", file_name="rx.jpg")])
        pipeline = build_pipeline(policy_repository, ai_provider=_FailingAIProvider())
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        await pipeline.run(claim, tracer=tracer)

        doc_verification_failed = [
            e for e in tracer.events
            if e.component == TraceComponent.DOCUMENT_VERIFICATION and e.event_type == TraceEventType.FAILED
        ]
        assert len(doc_verification_failed) == 1
        assert doc_verification_failed[0].error is not None
        assert doc_verification_failed[0].error.error_type == "AITimeoutError"
        assert doc_verification_failed[0].error.recoverable is True

        pipeline_failed = [
            e for e in tracer.events
            if e.component == TraceComponent.PIPELINE and e.event_type == TraceEventType.FAILED
        ]
        assert len(pipeline_failed) == 1
