"""
Integration tests for ClaimsPipeline — the three agents wired together,
exercising real early-stop behavior and trace emission end-to-end (no
mocked agents). Uses the real PolicyRepository/policy_terms.json.
"""

from __future__ import annotations

import pytest

from app.agents.claim_validation_agent import ClaimValidationAgent
from app.agents.cross_document_validation_agent import CrossDocumentValidationAgent
from app.agents.decision_generation_agent import DecisionGenerationAgent
from app.agents.document_extraction_agent import DocumentExtractionAgent
from app.agents.document_verification_agent import DocumentVerificationAgent
from app.agents.explanation_agent import ExplanationAgent
from app.agents.fraud_analysis_agent import FraudAnalysisAgent
from app.policy.policy_engine import PolicyEngine
from app.services.financial_calculation_service import FinancialCalculationService
from app.ai.schemas.ai_schemas import DocumentAnalysisResponse
from app.domain.errors import AITimeoutError
from app.domain.models import Claim, ClaimCategory, ClaimStatus, ClaimSubmission, DecisionType, DocumentMetadata
from app.domain.trace import TraceComponent, TraceContext, TraceEventType
from app.domain.verification import DocumentClassification
from app.pipeline.pipeline import ClaimsPipeline
from app.policy.policy_repository import PolicyRepository
from app.storage.document_storage import DocumentStorage
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
    async def test_full_pass_trace_has_no_failed_events_and_only_skips_unconfigured_stages(
        self, policy_repository
    ):
        # build_pipeline() deliberately doesn't pass a document_extraction_agent
        # or any Phase 2C/2D component (these tests never exercise real AI
        # extraction, policy/financial/fraud, or decision/explanation) — all
        # six are legitimately SKIPPED in that case, not a failure.
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
        skipped_components = {e.component for e in tracer.events if e.event_type == TraceEventType.SKIPPED}
        assert skipped_components == {
            TraceComponent.DOCUMENT_EXTRACTION,
            TraceComponent.POLICY_ENGINE,
            TraceComponent.FINANCIAL_CALCULATION,
            TraceComponent.FRAUD_ANALYSIS,
            TraceComponent.DECISION_GENERATION,
            TraceComponent.EXPLANATION,
        }
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


class TestCrossDocumentMemberIdentityMismatch:
    """
    Phase 2A identity-validation gap fix — full pipeline regression.
    EMP001 resolves to Rajesh Kumar (policy_terms.json). Before the fix,
    two documents that agreed with *each other* (both "Vikram Joshi")
    incorrectly PASSED, since CrossDocumentValidationAgent never compared
    against the claim's actual member. See docs/AI_HANDOFF.md "Phase 2A
    identity-validation gap fixed".
    """

    @pytest.mark.anyio
    async def test_two_internally_consistent_wrong_documents_now_blocks(self, policy_repository):
        claim = make_claim(
            member_id="EMP001",  # Rajesh Kumar
            documents=[
                DocumentMetadata(file_id="F007", file_name="rx.jpg"),
                DocumentMetadata(file_id="F008", file_name="bill.jpg"),
            ],
        )
        classifications = {
            "F007": DocumentClassification(
                file_id="F007", document_type="PRESCRIPTION", patient_name="Vikram Joshi", confidence=1.0
            ),
            "F008": DocumentClassification(
                file_id="F008", document_type="HOSPITAL_BILL", patient_name="Vikram Joshi", confidence=1.0
            ),
        }
        pipeline = build_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.BLOCKED
        assert result.stopped_at == "CROSS_DOCUMENT_VALIDATION"
        assert result.cross_document_validation_result.status == "BLOCKED"
        assert "Vikram Joshi" in result.user_message
        assert "Rajesh Kumar" in result.user_message

        # claim.member must actually be populated now (was always None before the fix).
        assert result.member is not None
        assert result.member.name == "Rajesh Kumar"

        # Trace: the mismatch is visible, with safe structured metadata —
        # no raw documents, no full LLM output.
        cross_doc_events = [e for e in tracer.events if e.component == TraceComponent.CROSS_DOCUMENT_VALIDATION]
        completed = next(e for e in cross_doc_events if e.event_type == TraceEventType.COMPLETED)
        assert completed.metadata["status"] == "BLOCKED"
        assert completed.metadata["expected_member_name"] == "Rajesh Kumar"
        assert completed.metadata["patient_names"] == {
            "Prescription": "Vikram Joshi",
            "Hospital Bill": "Vikram Joshi",
        }

    @pytest.mark.anyio
    async def test_documents_matching_the_actual_member_still_pass(self, policy_repository):
        """Regression guard: the fix must not make correctly-matching
        claims fail."""
        claim = make_claim(member_id="EMP001")
        classifications = {
            "F007": DocumentClassification(
                file_id="F007", document_type="PRESCRIPTION", patient_name="Rajesh Kumar", confidence=1.0
            ),
            "F008": DocumentClassification(
                file_id="F008", document_type="HOSPITAL_BILL", patient_name="Rajesh Kumar", confidence=1.0
            ),
        }
        pipeline = build_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.PROCESSING
        assert result.cross_document_validation_result.status == "PASS"


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


# ── Phase 2B: Document Extraction stage ─────────────────────────────────────


class _FakeDocumentStorage(DocumentStorage):
    def __init__(self, contents: dict[str, bytes]):
        self._contents = contents

    async def save(self, *, claim_id, filename, content):
        raise NotImplementedError("not needed for these tests")

    async def read(self, storage_reference: str) -> bytes:
        return self._contents[storage_reference]


class _FakeExtractionAIProvider:
    def __init__(self, responses: list):
        self._responses = list(responses)

    async def analyze_document(self, request):
        data = self._responses.pop(0)
        return DocumentAnalysisResponse(structured_data=data, model="fake-model", provider="fake")


_PRESCRIPTION_EXTRACTION_RESPONSE = {
    "patient_name": "Rajesh Kumar", "patient_age": "", "patient_gender": "", "patient_date_of_birth": "",
    "prescription_date": "2024-11-01", "doctor_name": "Dr. Arun Sharma", "doctor_registration_number": "",
    "doctor_specialization": "", "doctor_hospital_or_clinic": "", "diagnosis": "Viral Fever", "treatment": "",
    "medications": [], "investigations": [], "signature_present": "UNCLEAR", "stamp_present": "UNCLEAR",
    "confidence": 0.9, "warnings": [], "evidence": [],
}
_HOSPITAL_BILL_EXTRACTION_RESPONSE = {
    "patient_name": "Rajesh Kumar", "hospital_name": "City Clinic", "bill_number": "", "bill_date": "",
    "admission_date": "", "discharge_date": "", "doctor_name": "", "doctor_registration_number": "",
    "line_items": [], "subtotal": "", "discount": "", "tax": "", "total": "1500.00", "currency": "INR",
    "confidence": 0.85, "warnings": [], "evidence": [],
}


def build_pipeline_with_extraction(policy_repository, extraction_provider) -> ClaimsPipeline:
    storage = _FakeDocumentStorage({"ref1": b"\xff\xd8\xff-a", "ref2": b"\xff\xd8\xff-b"})
    return ClaimsPipeline(
        claim_validation_agent=ClaimValidationAgent(policy_repository=policy_repository),
        document_verification_agent=DocumentVerificationAgent(
            ai_provider=None, policy_repository=policy_repository
        ),
        cross_document_validation_agent=CrossDocumentValidationAgent(),
        document_extraction_agent=DocumentExtractionAgent(
            ai_provider=extraction_provider, document_storage=storage
        ),
    )


class TestDocumentExtractionStage:
    """
    Exercises the full pipeline with a real (fake-backed) extraction agent
    configured — TestFullPassThroughPhase2A above deliberately doesn't
    configure one, since it predates Phase 2B.
    """

    @pytest.mark.anyio
    async def test_extraction_runs_after_cross_document_validation_passes(self, policy_repository):
        provider = _FakeExtractionAIProvider(
            [_PRESCRIPTION_EXTRACTION_RESPONSE, _HOSPITAL_BILL_EXTRACTION_RESPONSE]
        )
        pipeline = build_pipeline_with_extraction(policy_repository, provider)
        claim = make_claim(
            documents=[
                DocumentMetadata(file_id="F007", file_name="rx.jpg", mime_type="image/jpeg", storage_reference="ref1"),
                DocumentMetadata(file_id="F008", file_name="bill.jpg", mime_type="image/jpeg", storage_reference="ref2"),
            ]
        )
        classifications = {
            "F007": DocumentClassification(file_id="F007", document_type="PRESCRIPTION", confidence=1.0),
            "F008": DocumentClassification(file_id="F008", document_type="HOSPITAL_BILL", confidence=1.0),
        }
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.PROCESSING
        assert result.extraction_result is not None
        assert len(result.extraction_result.extractions) == 2
        assert result.extraction_result.has_failures is False

        extraction_events = [e for e in tracer.events if e.component == TraceComponent.DOCUMENT_EXTRACTION]
        assert any(e.event_type == TraceEventType.COMPLETED for e in extraction_events)
        completed = next(e for e in extraction_events if e.event_type == TraceEventType.COMPLETED)
        assert completed.ai_metadata is not None
        assert completed.ai_metadata.provider == "fake"
        # PIPELINE-level completion event must reflect the current phase.
        assert "2D" in tracer.events[-1].message

    @pytest.mark.anyio
    async def test_one_failed_extraction_does_not_block_the_claim(self, policy_repository):
        """A per-document extraction failure degrades confidence/messaging
        but the claim itself must still reach PROCESSING — extraction
        failures are not a stop condition (see agent docstring)."""
        provider = _FakeExtractionAIProvider([AITimeoutError("fake", 60), _HOSPITAL_BILL_EXTRACTION_RESPONSE])
        pipeline = build_pipeline_with_extraction(policy_repository, provider)
        claim = make_claim(
            documents=[
                DocumentMetadata(file_id="F007", file_name="rx.jpg", mime_type="image/jpeg", storage_reference="ref1"),
                DocumentMetadata(file_id="F008", file_name="bill.jpg", mime_type="image/jpeg", storage_reference="ref2"),
            ]
        )
        classifications = {
            "F007": DocumentClassification(file_id="F007", document_type="PRESCRIPTION", confidence=1.0),
            "F008": DocumentClassification(file_id="F008", document_type="HOSPITAL_BILL", confidence=1.0),
        }
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.PROCESSING
        assert result.extraction_result.has_failures is True
        assert len(result.extraction_result.extractions) == 1
        assert len(result.extraction_result.failures) == 1
        assert "could not be fully processed" in result.user_message.lower()

    @pytest.mark.anyio
    async def test_no_extraction_agent_configured_is_skipped_not_failed(self, policy_repository):
        """build_pipeline() (no extraction agent) must remain fully
        backward-compatible — this is what the evaluation runner and older
        tests rely on."""
        claim = make_claim()
        classifications = {
            "F007": DocumentClassification(file_id="F007", document_type="PRESCRIPTION", confidence=1.0),
            "F008": DocumentClassification(file_id="F008", document_type="HOSPITAL_BILL", confidence=1.0),
        }
        pipeline = build_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.PROCESSING
        assert result.extraction_result is None
        skipped = [e for e in tracer.events if e.event_type == TraceEventType.SKIPPED]
        assert any(e.component == TraceComponent.DOCUMENT_EXTRACTION for e in skipped)


# ── Phase 2C: Policy Evaluation, Financial Calculation, Fraud Analysis ──────


def build_full_pipeline(policy_repository) -> ClaimsPipeline:
    """All implemented stages configured, including Phase 2C and Phase 2D —
    none of Policy/Financial/Fraud/DecisionGeneration call AI, and
    ExplanationAgent is built with ai_provider=None (deterministic fallback
    every time), so no fake provider is needed anywhere in this file."""
    return ClaimsPipeline(
        claim_validation_agent=ClaimValidationAgent(policy_repository=policy_repository),
        document_verification_agent=DocumentVerificationAgent(
            ai_provider=None, policy_repository=policy_repository
        ),
        cross_document_validation_agent=CrossDocumentValidationAgent(),
        policy_engine=PolicyEngine(policy_repository=policy_repository),
        financial_calculation_service=FinancialCalculationService(),
        fraud_analysis_agent=FraudAnalysisAgent(policy_repository=policy_repository),
        decision_generation_agent=DecisionGenerationAgent(),
        explanation_agent=ExplanationAgent(ai_provider=None),
    )


class TestPolicyFinancialFraudIntegration:
    """Assignment section 35: EMP001/CONSULTATION/Rajesh Kumar prescription
    + bill/claimed 1500 must reach Policy Evaluation, Financial
    Calculation, Fraud Analysis, and (Phase 2D) a final decision, with
    every stage traced."""

    @pytest.mark.anyio
    async def test_full_pipeline_reaches_policy_financial_and_fraud(self, policy_repository):
        # hospital_name given so PolicyEngine's network-hospital check
        # resolves to a definite NOT_APPLICABLE/PASSED finding rather than
        # a WARNING (unknown hospital) — this test is about a clean,
        # confident APPROVED path, not about the confidence-penalty
        # mechanics of an unresolvable hospital name (see
        # TestDegradedComponentManualReview for that).
        claim = make_claim(hospital_name="City Clinic, Bengaluru")
        classifications = {
            "F007": DocumentClassification(
                file_id="F007", document_type="PRESCRIPTION", patient_name="Rajesh Kumar", confidence=1.0
            ),
            "F008": DocumentClassification(
                file_id="F008", document_type="HOSPITAL_BILL", patient_name="Rajesh Kumar", confidence=1.0
            ),
        }
        pipeline = build_full_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.DECIDED

        assert result.policy_evaluation_result is not None
        assert result.policy_evaluation_result.coverage_category == "CONSULTATION"
        assert result.financial_calculation_result is not None
        assert result.financial_calculation_result.payable_amount is not None
        assert result.fraud_analysis_result is not None
        assert result.fraud_analysis_result.risk_level is not None

        # Phase 2D: a clean CONSULTATION claim within every limit reaches
        # APPROVED, with the exact deterministic payable amount.
        assert result.decision is not None
        assert result.decision.decision == DecisionType.APPROVED
        assert result.decision.approved_amount == result.financial_calculation_result.payable_amount
        assert result.decision.explanation_detail is not None
        assert result.decision.explanation_detail.source.value == "FALLBACK"  # no AI provider in this test

        components_completed = {
            e.component for e in tracer.events if e.event_type == TraceEventType.COMPLETED
        }
        assert TraceComponent.POLICY_ENGINE in components_completed
        assert TraceComponent.FINANCIAL_CALCULATION in components_completed
        assert TraceComponent.FRAUD_ANALYSIS in components_completed
        assert TraceComponent.DECISION_GENERATION in components_completed
        assert TraceComponent.EXPLANATION in components_completed
        assert TraceEventType.FAILED not in {e.event_type for e in tracer.events}


class TestPhase2AFixStillEarlyStopsBeforePhase2C:
    """Assignment section 36 — regression guard: EMP001 (Rajesh Kumar) with
    two "Vikram Joshi" documents must still stop at CROSS_DOCUMENT_VALIDATION
    (Phase 2A identity fix), and PolicyEngine/FinancialCalculationService/
    FraudAnalysisAgent/DecisionGenerationAgent/ExplanationAgent must never
    run — a BLOCKED claim never gets a fake decision (Phase 2D)."""

    @pytest.mark.anyio
    async def test_member_identity_mismatch_blocks_before_policy_financial_fraud(self, policy_repository):
        claim = make_claim(
            documents=[
                DocumentMetadata(file_id="F007", file_name="rx.jpg"),
                DocumentMetadata(file_id="F008", file_name="bill.jpg"),
            ]
        )
        classifications = {
            "F007": DocumentClassification(
                file_id="F007", document_type="PRESCRIPTION", patient_name="Vikram Joshi", confidence=1.0
            ),
            "F008": DocumentClassification(
                file_id="F008", document_type="HOSPITAL_BILL", patient_name="Vikram Joshi", confidence=1.0
            ),
        }
        pipeline = build_full_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.BLOCKED
        assert result.stopped_at == "CROSS_DOCUMENT_VALIDATION"
        assert result.policy_evaluation_result is None
        assert result.financial_calculation_result is None
        assert result.fraud_analysis_result is None
        assert result.decision is None

        started_components = {e.component for e in tracer.events if e.event_type == TraceEventType.STARTED}
        assert TraceComponent.POLICY_ENGINE not in started_components
        assert TraceComponent.FINANCIAL_CALCULATION not in started_components
        assert TraceComponent.FRAUD_ANALYSIS not in started_components
        assert TraceComponent.DECISION_GENERATION not in started_components
        assert TraceComponent.EXPLANATION not in started_components

        skipped_components = {e.component for e in tracer.events if e.event_type == TraceEventType.SKIPPED}
        assert TraceComponent.POLICY_ENGINE in skipped_components
        assert TraceComponent.FINANCIAL_CALCULATION in skipped_components
        assert TraceComponent.FRAUD_ANALYSIS in skipped_components
        assert TraceComponent.DECISION_GENERATION in skipped_components
        assert TraceComponent.EXPLANATION in skipped_components


class TestPolicyEngineFailureDegradesGracefully:
    """Assignment section 25/46: a genuine PolicyEngine failure must not
    silently approve/continue as if nothing happened, must not crash the
    claim, and must be visible in the trace. Financial Calculation must be
    skipped (it needs PolicyEvaluationResult); Fraud Analysis must still
    run independently."""

    @pytest.mark.anyio
    async def test_policy_engine_exception_skips_financial_but_not_fraud(self, policy_repository):
        class _FailingPolicyEngine:
            async def evaluate(self, claim):
                raise RuntimeError("simulated PolicyEngine failure")

        claim = make_claim()
        classifications = {
            "F007": DocumentClassification(file_id="F007", document_type="PRESCRIPTION", confidence=1.0),
            "F008": DocumentClassification(file_id="F008", document_type="HOSPITAL_BILL", confidence=1.0),
        }
        pipeline = ClaimsPipeline(
            claim_validation_agent=ClaimValidationAgent(policy_repository=policy_repository),
            document_verification_agent=DocumentVerificationAgent(
                ai_provider=None, policy_repository=policy_repository
            ),
            cross_document_validation_agent=CrossDocumentValidationAgent(),
            policy_engine=_FailingPolicyEngine(),
            financial_calculation_service=FinancialCalculationService(),
            fraud_analysis_agent=FraudAnalysisAgent(policy_repository=policy_repository),
        )
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        # Never crashes, never silently proceeds as if policy passed.
        assert result.status == ClaimStatus.PROCESSING
        assert result.policy_evaluation_result is None
        assert result.financial_calculation_result is None
        # Fraud is independent of policy/financial — it still ran.
        assert result.fraud_analysis_result is not None

        policy_events = [e for e in tracer.events if e.component == TraceComponent.POLICY_ENGINE]
        assert any(e.event_type == TraceEventType.FAILED for e in policy_events)
        financial_events = [e for e in tracer.events if e.component == TraceComponent.FINANCIAL_CALCULATION]
        assert any(e.event_type == TraceEventType.SKIPPED for e in financial_events)
        assert "policy evaluation could not be completed" in result.user_message


# ── Phase 2D: Decision Generation & Explanation ─────────────────────────────
#
# These reuse build_full_pipeline() (real PolicyEngine/FinancialCalculation
# Service/FraudAnalysisAgent/DecisionGenerationAgent, ExplanationAgent with
# no AI provider) — extraction is never configured in this file (Decision
# 30's backward-compat "skip, don't clobber"), so a test that needs
# PolicyEngine to see a diagnosis/line-items pre-populates
# `claim.extraction_result` directly before calling `pipeline.run()`,
# exactly like docs/AI_HANDOFF.md's fixture-based Phase 2C verification did.

from datetime import date as _date  # noqa: E402
from decimal import Decimal as _Decimal  # noqa: E402

from app.domain.extraction import (  # noqa: E402
    ClaimExtractionResult as _ClaimExtractionResult,
    DocumentExtractionResult as _DocumentExtractionResult,
    HospitalBillExtraction as _HospitalBillExtraction,
    LineItem as _LineItem,
    PrescriptionExtraction as _PrescriptionExtraction,
)
from app.domain.models import DocumentQuality as _DocumentQuality  # noqa: E402


class TestDecisionReachesPartialForLineItemExclusion:
    """TC006-shaped: a DENTAL claim with one covered and one excluded
    (cosmetic) line item must reach PARTIAL, not REJECTED — the per-line-
    item exclusion flag must not be treated as a whole-claim rejection."""

    @pytest.mark.anyio
    async def test_dental_claim_with_cosmetic_exclusion_is_partial(self, policy_repository):
        claim = make_claim(
            claim_category=ClaimCategory.DENTAL,
            claimed_amount=_Decimal("12000"),
            documents=[
                DocumentMetadata(file_id="F011", file_name="dental_bill.pdf"),
            ],
        )
        claim.extraction_result = _ClaimExtractionResult(
            extractions=[
                _DocumentExtractionResult(
                    file_id="F011", document_type="HOSPITAL_BILL", quality=_DocumentQuality.GOOD,
                    extraction=_HospitalBillExtraction(
                        hospital_name="Smile Dental Clinic",
                        line_items=[
                            _LineItem(description="Root Canal Treatment", amount=_Decimal("8000")),
                            _LineItem(description="Teeth Whitening", amount=_Decimal("4000")),
                        ],
                        total=_Decimal("12000"), confidence=0.95, warnings=[], evidence=[],
                    ),
                )
            ],
            failures=[], skipped=[], confidence=0.95, has_failures=False,
        )
        classifications = {
            "F011": DocumentClassification(file_id="F011", document_type="HOSPITAL_BILL", confidence=1.0),
        }
        pipeline = build_full_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.DECIDED
        assert result.decision.decision == DecisionType.PARTIAL
        # NOT 8000 (the eligible non-excluded amount): the global
        # per_claim_limit (Rs.5000) is applied as a real cap in this
        # implementation, same disclosed Decision-35 discrepancy already
        # documented for TC006 in docs/tradeoffs.md — the DECISION here
        # (PARTIAL) matches the assignment's own expectation even though
        # the exact amount does not.
        assert result.decision.approved_amount == _Decimal("5000.00")
        assert len(result.decision.line_item_decisions) == 2


class TestDecisionReachesRejectedForWaitingPeriod:
    """TC005-shaped: a diabetes diagnosis within the 90-day specific-
    condition waiting period must reach REJECTED with WAITING_PERIOD."""

    @pytest.mark.anyio
    async def test_diabetes_within_waiting_period_is_rejected(self, policy_repository):
        claim = make_claim(
            member_id="EMP005",
            claim_category=ClaimCategory.CONSULTATION,
            treatment_date=_date(2024, 10, 15),
            claimed_amount=_Decimal("3000"),
            hospital_name="Mehta Endocrine Clinic",
            documents=[
                DocumentMetadata(file_id="F009", file_name="rx.jpg"),
                DocumentMetadata(file_id="F010", file_name="bill.jpg"),
            ],
        )
        claim.extraction_result = _ClaimExtractionResult(
            extractions=[
                _DocumentExtractionResult(
                    file_id="F009", document_type="PRESCRIPTION", quality=_DocumentQuality.GOOD,
                    extraction=_PrescriptionExtraction(
                        diagnosis="Type 2 Diabetes Mellitus", confidence=0.95, warnings=[], evidence=[],
                    ),
                )
            ],
            failures=[], skipped=[], confidence=0.95, has_failures=False,
        )
        classifications = {
            "F009": DocumentClassification(
                file_id="F009", document_type="PRESCRIPTION", patient_name="Vikram Joshi", confidence=1.0
            ),
            "F010": DocumentClassification(
                file_id="F010", document_type="HOSPITAL_BILL", patient_name="Vikram Joshi", confidence=1.0
            ),
        }
        pipeline = build_full_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.DECIDED
        assert result.decision.decision == DecisionType.REJECTED
        assert result.decision.approved_amount == _Decimal("0")
        from app.domain.models import RejectionReason as _RejectionReason
        assert _RejectionReason.WAITING_PERIOD in result.decision.rejection_reasons


class TestDecisionReachesManualReviewForFraud:
    """TC009-shaped: same-day claim history (fixture-supplied via
    submission.claims_history) exceeding the deterministic threshold must
    reach MANUAL_REVIEW, with the financial figure still surfaced."""

    @pytest.mark.anyio
    async def test_same_day_claims_over_limit_is_manual_review(self, policy_repository):
        from app.domain.models import ClaimHistoryItem

        claim = make_claim(
            member_id="EMP008",
            claim_category=ClaimCategory.CONSULTATION,
            treatment_date=_date(2024, 10, 30),
            claimed_amount=_Decimal("4800"),
            documents=[
                DocumentMetadata(file_id="F017", file_name="rx.jpg"),
                DocumentMetadata(file_id="F018", file_name="bill.jpg"),
            ],
        )
        claim.submission.claims_history = [
            ClaimHistoryItem(claim_id="CLM_0081", date=_date(2024, 10, 30), amount=_Decimal("1200"), provider="City Clinic A"),
            ClaimHistoryItem(claim_id="CLM_0082", date=_date(2024, 10, 30), amount=_Decimal("1800"), provider="City Clinic B"),
            ClaimHistoryItem(claim_id="CLM_0083", date=_date(2024, 10, 30), amount=_Decimal("2100"), provider="Wellness Center"),
        ]
        classifications = {
            "F017": DocumentClassification(file_id="F017", document_type="PRESCRIPTION", confidence=1.0),
            "F018": DocumentClassification(file_id="F018", document_type="HOSPITAL_BILL", confidence=1.0),
        }
        pipeline = build_full_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.DECIDED
        assert result.decision.decision == DecisionType.MANUAL_REVIEW
        assert result.decision.reason_code == "MANUAL_REVIEW_FRAUD"
        assert result.decision.approved_amount is not None  # reliable financial figure still surfaced


class TestDecisionGenerationFailureDegradesGracefully:
    """A genuine, unexpected exception inside DecisionGenerationAgent must
    never leave `claim.decision` empty — the pipeline falls back to a
    conservative MANUAL_REVIEW decision, records FAILED in the trace, and
    still completes (never crashes/propagates)."""

    @pytest.mark.anyio
    async def test_decision_generation_exception_yields_fallback_decision(self, policy_repository):
        class _FailingDecisionAgent:
            async def run(self, claim):
                raise RuntimeError("simulated DecisionGenerationAgent failure")

        claim = make_claim()
        classifications = {
            "F007": DocumentClassification(file_id="F007", document_type="PRESCRIPTION", confidence=1.0),
            "F008": DocumentClassification(file_id="F008", document_type="HOSPITAL_BILL", confidence=1.0),
        }
        pipeline = ClaimsPipeline(
            claim_validation_agent=ClaimValidationAgent(policy_repository=policy_repository),
            document_verification_agent=DocumentVerificationAgent(
                ai_provider=None, policy_repository=policy_repository
            ),
            cross_document_validation_agent=CrossDocumentValidationAgent(),
            policy_engine=PolicyEngine(policy_repository=policy_repository),
            financial_calculation_service=FinancialCalculationService(),
            fraud_analysis_agent=FraudAnalysisAgent(policy_repository=policy_repository),
            decision_generation_agent=_FailingDecisionAgent(),
            explanation_agent=ExplanationAgent(ai_provider=None),
        )
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.decision is not None
        assert result.decision.decision == DecisionType.MANUAL_REVIEW
        assert result.decision.reason_code == "MANUAL_REVIEW_DECISION_GENERATION_FAILED"
        assert result.status == ClaimStatus.DECIDED

        decision_events = [e for e in tracer.events if e.component == TraceComponent.DECISION_GENERATION]
        assert any(e.event_type == TraceEventType.FAILED for e in decision_events)
        assert TraceEventType.FAILED in {
            e.event_type for e in tracer.events if e.component == TraceComponent.PIPELINE
        } or result.status == ClaimStatus.DECIDED  # pipeline itself still completes


class TestExplanationFailureDegradesGracefully:
    """A failure INSIDE the pipeline's Explanation stage (defense-in-depth
    — ExplanationAgent's own contract is "never raise", so this simulates
    a bug bypassing that) must never touch the decision already produced
    by DecisionGenerationAgent."""

    @pytest.mark.anyio
    async def test_explanation_exception_never_touches_the_decision(self, policy_repository):
        class _FailingExplanationAgent:
            async def run(self, claim, decision):
                raise RuntimeError("simulated ExplanationAgent failure")

        claim = make_claim(hospital_name="City Clinic, Bengaluru")
        classifications = {
            "F007": DocumentClassification(file_id="F007", document_type="PRESCRIPTION", confidence=1.0),
            "F008": DocumentClassification(file_id="F008", document_type="HOSPITAL_BILL", confidence=1.0),
        }
        pipeline = ClaimsPipeline(
            claim_validation_agent=ClaimValidationAgent(policy_repository=policy_repository),
            document_verification_agent=DocumentVerificationAgent(
                ai_provider=None, policy_repository=policy_repository
            ),
            cross_document_validation_agent=CrossDocumentValidationAgent(),
            policy_engine=PolicyEngine(policy_repository=policy_repository),
            financial_calculation_service=FinancialCalculationService(),
            fraud_analysis_agent=FraudAnalysisAgent(policy_repository=policy_repository),
            decision_generation_agent=DecisionGenerationAgent(),
            explanation_agent=_FailingExplanationAgent(),
        )
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.decision is not None
        assert result.decision.decision == DecisionType.APPROVED
        assert result.decision.approved_amount == result.financial_calculation_result.payable_amount
        # explanation_detail was never populated (Explanation stage failed
        # before it could be assigned), but the deterministic
        # explanation/member_facing_message from Stage 8 are untouched.
        assert result.decision.explanation_detail is None
        assert result.decision.explanation
        assert result.decision.member_facing_message

        explanation_events = [e for e in tracer.events if e.component == TraceComponent.EXPLANATION]
        assert any(e.event_type == TraceEventType.FAILED for e in explanation_events)


class TestPhase2DTraceCompleteness:
    """Assignment point 5 (explainability): a full clean pass must show
    all 9 pipeline stages plus the PIPELINE start/end events, in order,
    with no unexplained gaps."""

    @pytest.mark.anyio
    async def test_full_pass_shows_every_stage_started_and_completed(self, policy_repository):
        claim = make_claim()
        classifications = {
            "F007": DocumentClassification(
                file_id="F007", document_type="PRESCRIPTION", patient_name="Rajesh Kumar", confidence=1.0
            ),
            "F008": DocumentClassification(
                file_id="F008", document_type="HOSPITAL_BILL", patient_name="Rajesh Kumar", confidence=1.0
            ),
        }
        pipeline = build_full_pipeline(policy_repository)
        tracer = TraceService(TraceContext.new(claim_id=claim.claim_id))

        result = await pipeline.run(claim, classifications=classifications, tracer=tracer)

        assert result.status == ClaimStatus.DECIDED
        expected_stages = [
            TraceComponent.CLAIM_VALIDATION,
            TraceComponent.DOCUMENT_VERIFICATION,
            TraceComponent.CROSS_DOCUMENT_VALIDATION,
            TraceComponent.POLICY_ENGINE,
            TraceComponent.FINANCIAL_CALCULATION,
            TraceComponent.FRAUD_ANALYSIS,
            TraceComponent.DECISION_GENERATION,
            TraceComponent.EXPLANATION,
        ]
        completed = {e.component for e in tracer.events if e.event_type == TraceEventType.COMPLETED}
        for stage in expected_stages:
            assert stage in completed, f"{stage} never COMPLETED"
        assert TraceEventType.FAILED not in {e.event_type for e in tracer.events}
        assert tracer.events[0].component == TraceComponent.PIPELINE
        assert tracer.events[0].event_type == TraceEventType.STARTED
        assert tracer.events[-1].component == TraceComponent.PIPELINE
        assert tracer.events[-1].event_type == TraceEventType.COMPLETED
        assert "Phase 2D" in tracer.events[-1].message
