"""
Integration tests for ClaimsPipeline — the three agents wired together,
exercising real early-stop behavior and trace emission end-to-end (no
mocked agents). Uses the real PolicyRepository/policy_terms.json.
"""

from __future__ import annotations

import pytest

from app.agents.claim_validation_agent import ClaimValidationAgent
from app.agents.cross_document_validation_agent import CrossDocumentValidationAgent
from app.agents.document_extraction_agent import DocumentExtractionAgent
from app.agents.document_verification_agent import DocumentVerificationAgent
from app.ai.schemas.ai_schemas import DocumentAnalysisResponse
from app.domain.errors import AITimeoutError
from app.domain.models import Claim, ClaimCategory, ClaimStatus, ClaimSubmission, DocumentMetadata
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
    async def test_full_pass_trace_has_no_failed_events_and_only_skips_unconfigured_extraction(
        self, policy_repository
    ):
        # build_pipeline() deliberately doesn't pass a document_extraction_agent
        # (these tests never exercise real AI extraction) — DOCUMENT_EXTRACTION
        # is legitimately SKIPPED in that case (Phase 2B), not a failure.
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
        assert skipped_components == {TraceComponent.DOCUMENT_EXTRACTION}
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
        # PIPELINE-level completion event must reflect Phase 2B, not Phase 2A.
        assert "2B" in tracer.events[-1].message

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
