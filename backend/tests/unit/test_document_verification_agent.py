"""
Unit tests for DocumentVerificationAgent.

Uses pre-supplied DocumentClassification objects (the "fixture" path) so
these tests don't need a real/mocked AI provider — that path is exercised
separately (see test_document_verification_agent_ai_path below, and the
live-API section of the phase report for the real-call verification).
"""

from __future__ import annotations

import pytest

from app.agents.document_verification_agent import DocumentVerificationAgent
from app.ai.schemas.ai_schemas import AIStructuredResponse, DocumentAnalysisResponse
from app.domain.errors import ExtractionError
from app.domain.models import ClaimCategory, DocumentMetadata, DocumentQuality, DocumentType
from app.domain.verification import DocumentClassification, DocumentVerificationStatus
from app.policy.policy_repository import PolicyRepository
from app.storage.document_storage import DocumentStorage


@pytest.fixture
def agent() -> DocumentVerificationAgent:
    return DocumentVerificationAgent(ai_provider=None, policy_repository=PolicyRepository())


@pytest.fixture
def anyio_backend():
    return "asyncio"


def classification(file_id, doc_type, quality=DocumentQuality.GOOD, patient_name=None):
    return DocumentClassification(
        file_id=file_id, document_type=doc_type, quality=quality, patient_name=patient_name,
        confidence=1.0, source="fixture",
    )


class TestCorrectDocuments:
    @pytest.mark.anyio
    async def test_all_required_documents_present_passes(self, agent):
        docs = [
            DocumentMetadata(file_id="F1", file_name="rx.jpg"),
            DocumentMetadata(file_id="F2", file_name="bill.jpg"),
        ]
        classifications = {
            "F1": classification("F1", DocumentType.PRESCRIPTION),
            "F2": classification("F2", DocumentType.HOSPITAL_BILL),
        }
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert result.status == DocumentVerificationStatus.PASS
        assert result.missing_documents == []


class TestMissingDocument:
    @pytest.mark.anyio
    async def test_missing_required_document_blocks(self, agent):
        docs = [DocumentMetadata(file_id="F1", file_name="rx1.jpg"), DocumentMetadata(file_id="F2", file_name="rx2.jpg")]
        classifications = {
            "F1": classification("F1", DocumentType.PRESCRIPTION),
            "F2": classification("F2", DocumentType.PRESCRIPTION),
        }
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert result.status == DocumentVerificationStatus.BLOCKED
        assert result.missing_documents == [DocumentType.HOSPITAL_BILL]

    @pytest.mark.anyio
    async def test_message_names_uploaded_and_required_types(self, agent):
        docs = [DocumentMetadata(file_id="F1", file_name="rx1.jpg")]
        classifications = {"F1": classification("F1", DocumentType.PRESCRIPTION)}
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert "Prescription" in result.user_message
        assert "Hospital Bill" in result.user_message


class TestWrongDocument:
    @pytest.mark.anyio
    async def test_document_outside_required_and_optional_is_flagged_wrong(self, agent):
        docs = [
            DocumentMetadata(file_id="F1", file_name="rx.jpg"),
            DocumentMetadata(file_id="F2", file_name="bill.jpg"),
            DocumentMetadata(file_id="F3", file_name="dental.jpg"),
        ]
        classifications = {
            "F1": classification("F1", DocumentType.PRESCRIPTION),
            "F2": classification("F2", DocumentType.HOSPITAL_BILL),
            "F3": classification("F3", DocumentType.DENTAL_REPORT),
        }
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        # All required docs present -> PASS overall, but the extraneous doc is still flagged.
        assert result.status == DocumentVerificationStatus.PASS
        assert DocumentType.DENTAL_REPORT in result.wrong_documents


class TestUnreadableDocument:
    @pytest.mark.anyio
    async def test_unreadable_document_needs_resubmission(self, agent):
        docs = [
            DocumentMetadata(file_id="F1", file_name="rx.jpg"),
            DocumentMetadata(file_id="F2", file_name="bill.jpg"),
        ]
        classifications = {
            "F1": classification("F1", DocumentType.PRESCRIPTION, quality=DocumentQuality.GOOD),
            "F2": classification("F2", DocumentType.PHARMACY_BILL, quality=DocumentQuality.UNREADABLE),
        }
        result = await agent.run(
            claim_category=ClaimCategory.PHARMACY, documents=docs, classifications=classifications
        )
        assert result.status == DocumentVerificationStatus.NEEDS_RESUBMISSION
        assert len(result.quality_issues) == 1
        assert result.quality_issues[0].file_id == "F2"

    @pytest.mark.anyio
    async def test_unreadable_message_identifies_the_specific_document(self, agent):
        docs = [DocumentMetadata(file_id="F2", file_name="bill.jpg")]
        classifications = {
            "F2": classification("F2", DocumentType.PHARMACY_BILL, quality=DocumentQuality.UNREADABLE)
        }
        result = await agent.run(
            claim_category=ClaimCategory.PHARMACY, documents=docs, classifications=classifications
        )
        assert "Pharmacy Bill" in result.user_message
        assert "re-upload" in result.user_message.lower()

    @pytest.mark.anyio
    async def test_partial_quality_also_needs_resubmission(self, agent):
        docs = [DocumentMetadata(file_id="F1", file_name="rx.jpg")]
        classifications = {"F1": classification("F1", DocumentType.PRESCRIPTION, quality=DocumentQuality.PARTIAL)}
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert result.status == DocumentVerificationStatus.NEEDS_RESUBMISSION

    @pytest.mark.anyio
    async def test_unreadable_is_not_treated_as_rejection(self, agent):
        """Explicit regression guard: unreadable != BLOCKED."""
        docs = [DocumentMetadata(file_id="F1", file_name="bill.jpg")]
        classifications = {"F1": classification("F1", DocumentType.PHARMACY_BILL, quality=DocumentQuality.UNREADABLE)}
        result = await agent.run(
            claim_category=ClaimCategory.PHARMACY, documents=docs, classifications=classifications
        )
        assert result.status != DocumentVerificationStatus.BLOCKED


class TestUnknownDocumentType:
    @pytest.mark.anyio
    async def test_unknown_type_treated_as_wrong_document(self, agent):
        docs = [
            DocumentMetadata(file_id="F1", file_name="rx.jpg"),
            DocumentMetadata(file_id="F2", file_name="bill.jpg"),
            DocumentMetadata(file_id="F3", file_name="mystery.jpg"),
        ]
        classifications = {
            "F1": classification("F1", DocumentType.PRESCRIPTION),
            "F2": classification("F2", DocumentType.HOSPITAL_BILL),
            "F3": classification("F3", DocumentType.UNKNOWN),
        }
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert DocumentType.UNKNOWN in result.wrong_documents


class _FakeAIProvider:
    """Minimal AIProvider double — no vendor SDK, just the interface shape
    DocumentVerificationAgent actually calls."""

    def __init__(self, structured_data: dict):
        self._data = structured_data
        self.calls = 0

    async def generate_structured(self, request):
        self.calls += 1
        return AIStructuredResponse(
            data=self._data, model="fake-model", provider="fake", raw_text=None
        )


class TestAIClassificationPath:
    """
    Covers documents with NO pre-supplied classification AND no
    storage_reference — the text-only fallback path (see
    TestRealContentClassificationPath below for the actual real-upload
    path, which reads real bytes). Uses a fake AIProvider double (not a
    vendor SDK)."""

    @pytest.mark.anyio
    async def test_calls_ai_provider_when_no_classification_supplied(self):
        fake_provider = _FakeAIProvider(
            {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.8}
        )
        agent = DocumentVerificationAgent(ai_provider=fake_provider, policy_repository=PolicyRepository())
        docs = [
            DocumentMetadata(file_id="F1", file_name="rx.jpg"),
            DocumentMetadata(file_id="F2", file_name="bill.jpg"),
        ]
        classifications = {"F2": classification("F2", DocumentType.HOSPITAL_BILL)}

        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )

        assert fake_provider.calls == 1  # only F1 needed classifying
        assert result.status == DocumentVerificationStatus.PASS
        ai_classified = next(c for c in result.classifications if c.file_id == "F1")
        assert ai_classified.source == "ai"
        assert ai_classified.document_type == DocumentType.PRESCRIPTION

    @pytest.mark.anyio
    async def test_malformed_ai_response_raises_extraction_error(self):
        fake_provider = _FakeAIProvider({"document_type": "NOT_A_REAL_TYPE", "quality": "GOOD", "confidence": 0.5})
        agent = DocumentVerificationAgent(ai_provider=fake_provider, policy_repository=PolicyRepository())
        docs = [DocumentMetadata(file_id="F1", file_name="rx.jpg")]

        with pytest.raises(ExtractionError):
            await agent.run(claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications={})

    @pytest.mark.anyio
    async def test_ai_call_metadata_captured_on_result(self):
        fake_provider = _FakeAIProvider(
            {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.8}
        )
        agent = DocumentVerificationAgent(ai_provider=fake_provider, policy_repository=PolicyRepository())
        docs = [DocumentMetadata(file_id="F1", file_name="rx.jpg")]

        result = await agent.run(claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications={})

        assert len(result.ai_calls) == 1
        assert result.ai_calls[0].provider == "fake"
        assert result.ai_calls[0].model == "fake-model"

    @pytest.mark.anyio
    async def test_no_ai_calls_recorded_when_every_document_is_pre_classified(self):
        docs = [DocumentMetadata(file_id="F1", file_name="rx.jpg")]
        classifications = {"F1": classification("F1", DocumentType.PRESCRIPTION)}
        agent = DocumentVerificationAgent(ai_provider=None, policy_repository=PolicyRepository())

        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )

        assert result.ai_calls == []


class TestConfidence:
    @pytest.mark.anyio
    async def test_overall_confidence_is_the_minimum_across_documents(self, agent):
        docs = [
            DocumentMetadata(file_id="F1", file_name="rx.jpg"),
            DocumentMetadata(file_id="F2", file_name="bill.jpg"),
        ]
        classifications = {
            "F1": DocumentClassification(file_id="F1", document_type=DocumentType.PRESCRIPTION, confidence=0.95),
            "F2": DocumentClassification(file_id="F2", document_type=DocumentType.HOSPITAL_BILL, confidence=0.6),
        }
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert result.confidence == 0.6

    @pytest.mark.anyio
    async def test_confidence_none_when_not_provided(self, agent):
        docs = [DocumentMetadata(file_id="F1", file_name="rx.jpg")]
        classifications = {
            "F1": DocumentClassification(file_id="F1", document_type=DocumentType.PRESCRIPTION, confidence=None)
        }
        result = await agent.run(
            claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications=classifications
        )
        assert result.confidence is None


class _FakeDocumentStorage(DocumentStorage):
    """In-memory DocumentStorage double — no real filesystem I/O."""

    def __init__(self, contents: dict[str, bytes]):
        self._contents = contents
        self.read_refs: list[str] = []

    async def save(self, *, claim_id: str, filename: str, content: bytes) -> str:
        raise NotImplementedError("not needed for these tests")

    async def read(self, storage_reference: str) -> bytes:
        self.read_refs.append(storage_reference)
        return self._contents[storage_reference]


class _FakeAnalyzeDocumentProvider:
    """AIProvider double implementing only analyze_document — proves
    DocumentVerificationAgent calls the real multimodal path, not
    generate_structured, when a document has actual stored bytes."""

    def __init__(self, structured_data: dict):
        self._data = structured_data
        self.calls = 0
        self.last_request = None

    async def analyze_document(self, request):
        self.calls += 1
        self.last_request = request
        return DocumentAnalysisResponse(
            structured_data=self._data, model="fake-vision-model", provider="fake"
        )

    async def generate_structured(self, request):  # pragma: no cover — must never be called
        raise AssertionError("real-content documents must use analyze_document, not generate_structured")


class TestRealContentClassificationPath:
    """
    The actual production path (Phase 2A correction): a document with a
    storage_reference is read from DocumentStorage and classified from its
    real bytes via AIProvider.analyze_document() — never from filename or
    declared_type alone.
    """

    @pytest.mark.anyio
    async def test_reads_real_bytes_and_calls_analyze_document(self):
        storage = _FakeDocumentStorage({"CLM-1/abc.jpg": b"\xff\xd8\xff-real-jpeg-bytes"})
        provider = _FakeAnalyzeDocumentProvider(
            {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "Rajesh Kumar", "confidence": 0.94}
        )
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [
            DocumentMetadata(
                file_id="abc", file_name="rx.jpg", mime_type="image/jpeg", storage_reference="CLM-1/abc.jpg"
            ),
            DocumentMetadata(
                file_id="def", file_name="bill.jpg", mime_type="image/jpeg", storage_reference="CLM-1/def.jpg"
            ),
        ]
        storage._contents["CLM-1/def.jpg"] = b"\xff\xd8\xff-another-real-jpeg"

        result = await agent.run(claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications={})

        assert provider.calls == 2
        assert storage.read_refs == ["CLM-1/abc.jpg", "CLM-1/def.jpg"]
        rx = next(c for c in result.classifications if c.file_id == "abc")
        assert rx.document_type == DocumentType.PRESCRIPTION
        assert rx.patient_name == "Rajesh Kumar"
        assert rx.confidence == 0.94

    @pytest.mark.anyio
    async def test_filename_alone_never_determines_type(self):
        """A file literally named 'prescription.jpg' whose real content the
        AI reads as a hospital bill must be classified as a hospital bill —
        the filename is context, never the answer."""
        storage = _FakeDocumentStorage({"CLM-1/x.jpg": b"\xff\xd8\xff-content"})
        provider = _FakeAnalyzeDocumentProvider(
            {"document_type": "HOSPITAL_BILL", "quality": "GOOD", "patient_name": "", "confidence": 0.9}
        )
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [
            DocumentMetadata(
                file_id="x", file_name="prescription.jpg", mime_type="image/jpeg", storage_reference="CLM-1/x.jpg"
            )
        ]

        result = await agent.run(claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications={})

        assert result.classifications[0].document_type == DocumentType.HOSPITAL_BILL

    @pytest.mark.anyio
    async def test_ai_metadata_captured_for_real_content_call(self):
        storage = _FakeDocumentStorage({"CLM-1/x.jpg": b"\xff\xd8\xff-content"})
        provider = _FakeAnalyzeDocumentProvider(
            {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.8}
        )
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [DocumentMetadata(file_id="x", file_name="rx.jpg", mime_type="image/jpeg", storage_reference="CLM-1/x.jpg")]

        result = await agent.run(claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications={})

        assert result.ai_calls[0].provider == "fake"
        assert result.ai_calls[0].model == "fake-vision-model"

    @pytest.mark.anyio
    async def test_no_structured_data_raises_extraction_error(self):
        storage = _FakeDocumentStorage({"CLM-1/x.jpg": b"\xff\xd8\xff-content"})
        provider = _FakeAnalyzeDocumentProvider(None)  # simulates a response with no structured_data
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [DocumentMetadata(file_id="x", file_name="rx.jpg", mime_type="image/jpeg", storage_reference="CLM-1/x.jpg")]

        with pytest.raises(ExtractionError):
            await agent.run(claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications={})


class TestBillVsReportClassificationOutcome:
    """
    A real, live classification failure was found: itemized BILL documents
    from dental/diagnostic specialties were being classified as that
    specialty's REPORT type instead of HOSPITAL_BILL, blocking claims that
    require a hospital bill. The prompt fix (app/ai/prompts/
    document_verification.py) is unit-tested for its own content in
    test_document_classification_prompt.py; these tests instead prove the
    *consequence*: once a document is correctly classified as HOSPITAL_BILL
    (whatever specialty issued it), DocumentVerificationAgent's own
    required-document matching accepts it — no new substitution/fuzzy-match
    logic was needed or added, only the classification input was wrong.
    No test-case ID or fixture file name is referenced here — every claim
    is built from a synthetic, generic document.
    """

    @pytest.mark.anyio
    async def test_dental_bill_classified_as_hospital_bill_satisfies_dental_category(self):
        """DENTAL requires HOSPITAL_BILL (policy_terms.json). A dental
        clinic's itemized treatment bill, correctly classified as
        HOSPITAL_BILL (not DENTAL_REPORT), must pass verification."""
        storage = _FakeDocumentStorage({"CLM-1/bill.pdf": b"%PDF-dental-bill-bytes"})
        provider = _FakeAnalyzeDocumentProvider(
            {"document_type": "HOSPITAL_BILL", "quality": "GOOD", "patient_name": "", "confidence": 0.9}
        )
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [DocumentMetadata(file_id="bill", file_name="bill.pdf", mime_type="application/pdf", storage_reference="CLM-1/bill.pdf")]

        result = await agent.run(claim_category=ClaimCategory.DENTAL, documents=docs, classifications={})

        assert result.status == DocumentVerificationStatus.PASS
        assert result.missing_documents == []
        assert result.classifications[0].document_type == DocumentType.HOSPITAL_BILL

    @pytest.mark.anyio
    async def test_diagnostic_center_bill_classified_as_hospital_bill_satisfies_diagnostic_requirement(self):
        """DIAGNOSTIC requires PRESCRIPTION + LAB_REPORT + HOSPITAL_BILL.
        A diagnostics center's itemized service bill, correctly classified
        as HOSPITAL_BILL (not DIAGNOSTIC_REPORT — which isn't even a valid
        requirement for this category), must be accepted as the bill."""
        storage = _FakeDocumentStorage({
            "CLM-1/rx.pdf": b"%PDF-rx", "CLM-1/lab.pdf": b"%PDF-lab", "CLM-1/bill.pdf": b"%PDF-diagnostic-bill",
        })
        provider = _FakeSequentialAnalyzeDocumentProvider([
            {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
            {"document_type": "LAB_REPORT", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
            {"document_type": "HOSPITAL_BILL", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
        ])
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [
            DocumentMetadata(file_id="rx", file_name="rx.pdf", mime_type="application/pdf", storage_reference="CLM-1/rx.pdf"),
            DocumentMetadata(file_id="lab", file_name="lab.pdf", mime_type="application/pdf", storage_reference="CLM-1/lab.pdf"),
            DocumentMetadata(file_id="bill", file_name="bill.pdf", mime_type="application/pdf", storage_reference="CLM-1/bill.pdf"),
        ]

        result = await agent.run(claim_category=ClaimCategory.DIAGNOSTIC, documents=docs, classifications={})

        assert result.status == DocumentVerificationStatus.PASS
        assert result.missing_documents == []
        bill = next(c for c in result.classifications if c.file_id == "bill")
        assert bill.document_type == DocumentType.HOSPITAL_BILL

    @pytest.mark.anyio
    async def test_genuine_diagnostic_report_still_classified_as_diagnostic_report(self):
        """The fix must not overcorrect: a document that is genuinely a
        narrative diagnostic report (findings/impression, no billing
        structure) must still classify as DIAGNOSTIC_REPORT, not be forced
        into HOSPITAL_BILL."""
        storage = _FakeDocumentStorage({"CLM-1/report.pdf": b"%PDF-diagnostic-report"})
        provider = _FakeAnalyzeDocumentProvider(
            {"document_type": "DIAGNOSTIC_REPORT", "quality": "GOOD", "patient_name": "", "confidence": 0.85}
        )
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [DocumentMetadata(file_id="report", file_name="report.pdf", mime_type="application/pdf", storage_reference="CLM-1/report.pdf")]

        result = await agent.run(claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications={})

        assert result.classifications[0].document_type == DocumentType.DIAGNOSTIC_REPORT

    @pytest.mark.anyio
    async def test_genuine_dental_report_still_classified_as_dental_report(self):
        """Same overcorrection guard for DENTAL_REPORT — a genuine clinical
        dental report (not a bill) must still classify correctly."""
        storage = _FakeDocumentStorage({"CLM-1/report.pdf": b"%PDF-dental-report"})
        provider = _FakeAnalyzeDocumentProvider(
            {"document_type": "DENTAL_REPORT", "quality": "GOOD", "patient_name": "", "confidence": 0.85}
        )
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [DocumentMetadata(file_id="report", file_name="report.pdf", mime_type="application/pdf", storage_reference="CLM-1/report.pdf")]

        result = await agent.run(claim_category=ClaimCategory.DENTAL, documents=docs, classifications={})

        assert result.classifications[0].document_type == DocumentType.DENTAL_REPORT

    @pytest.mark.anyio
    async def test_existing_prescription_classification_unaffected(self):
        storage = _FakeDocumentStorage({"CLM-1/rx.pdf": b"%PDF-rx"})
        provider = _FakeAnalyzeDocumentProvider(
            {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "Test Patient", "confidence": 0.95}
        )
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [DocumentMetadata(file_id="rx", file_name="rx.pdf", mime_type="application/pdf", storage_reference="CLM-1/rx.pdf")]

        result = await agent.run(claim_category=ClaimCategory.PHARMACY, documents=docs, classifications={})

        assert result.classifications[0].document_type == DocumentType.PRESCRIPTION

    @pytest.mark.anyio
    async def test_existing_generic_hospital_bill_classification_unaffected(self):
        storage = _FakeDocumentStorage({"CLM-1/bill.pdf": b"%PDF-generic-bill"})
        provider = _FakeAnalyzeDocumentProvider(
            {"document_type": "HOSPITAL_BILL", "quality": "GOOD", "patient_name": "", "confidence": 0.92}
        )
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [
            DocumentMetadata(file_id="rx", file_name="rx.pdf", mime_type="application/pdf", storage_reference="CLM-1/rx.pdf"),
            DocumentMetadata(file_id="bill", file_name="bill.pdf", mime_type="application/pdf", storage_reference="CLM-1/bill.pdf"),
        ]
        # Only classify the second document in this assertion; reuse the
        # existing single-response fake for the bill specifically.
        result = await agent.run(claim_category=ClaimCategory.CONSULTATION, documents=[docs[1]], classifications={})

        assert result.classifications[0].document_type == DocumentType.HOSPITAL_BILL

    @pytest.mark.anyio
    async def test_wrong_document_still_blocked_after_prompt_change(self):
        """A genuinely wrong document (e.g. a second prescription where a
        bill is required) must still be flagged wrong — the structural
        bill/report guidance must not weaken correct rejections."""
        storage = _FakeDocumentStorage({
            "CLM-1/rx1.pdf": b"%PDF-rx1", "CLM-1/rx2.pdf": b"%PDF-rx2",
        })
        provider = _FakeSequentialAnalyzeDocumentProvider([
            {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
            {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
        ])
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [
            DocumentMetadata(file_id="rx1", file_name="rx1.pdf", mime_type="application/pdf", storage_reference="CLM-1/rx1.pdf"),
            DocumentMetadata(file_id="rx2", file_name="rx2.pdf", mime_type="application/pdf", storage_reference="CLM-1/rx2.pdf"),
        ]

        result = await agent.run(claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications={})

        assert result.status == DocumentVerificationStatus.BLOCKED
        assert DocumentType.HOSPITAL_BILL in result.missing_documents


class TestLabReportVsDiagnosticReportClassificationOutcome:
    """
    A second real classification failure: a laboratory-issued report of an
    imaging test (e.g. an MRI reported by an accredited diagnostics lab,
    with a Sample ID and a TEST NAME/RESULT/UNIT/NORMAL RANGE layout) was
    classified as DIAGNOSTIC_REPORT instead of LAB_REPORT, so a DIAGNOSTIC
    claim (which requires PRESCRIPTION + LAB_REPORT + HOSPITAL_BILL) was
    blocked: LAB_REPORT reported missing, DIAGNOSTIC_REPORT reported wrong.
    The prompt fix distinguishes LAB_REPORT from DIAGNOSTIC_REPORT by who
    issued the report and how it's tracked, never by which medical test it
    describes (see test_document_classification_prompt.py for the
    prompt-content tests). These tests prove the *consequence* once
    classification is correct — and, as a regression guard, that no
    keyword-based substitution logic was added to the agent itself. No
    test-case ID or fixture file name is referenced; every claim here is
    built from synthetic, generic documents.
    """

    @pytest.mark.anyio
    async def test_lab_issued_imaging_report_classified_as_lab_report_satisfies_diagnostic_requirement(self):
        """DIAGNOSTIC requires PRESCRIPTION + LAB_REPORT + HOSPITAL_BILL.
        A laboratory's report of an imaging test, correctly classified as
        LAB_REPORT (not DIAGNOSTIC_REPORT) purely because the test happens
        to be imaging, must satisfy the LAB_REPORT requirement."""
        storage = _FakeDocumentStorage({
            "CLM-1/rx.pdf": b"%PDF-rx", "CLM-1/lab.pdf": b"%PDF-lab-issued-imaging-report",
            "CLM-1/bill.pdf": b"%PDF-bill",
        })
        provider = _FakeSequentialAnalyzeDocumentProvider([
            {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
            {"document_type": "LAB_REPORT", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
            {"document_type": "HOSPITAL_BILL", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
        ])
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [
            DocumentMetadata(file_id="rx", file_name="rx.pdf", mime_type="application/pdf", storage_reference="CLM-1/rx.pdf"),
            DocumentMetadata(file_id="lab", file_name="lab.pdf", mime_type="application/pdf", storage_reference="CLM-1/lab.pdf"),
            DocumentMetadata(file_id="bill", file_name="bill.pdf", mime_type="application/pdf", storage_reference="CLM-1/bill.pdf"),
        ]

        result = await agent.run(claim_category=ClaimCategory.DIAGNOSTIC, documents=docs, classifications={})

        assert result.status == DocumentVerificationStatus.PASS
        assert result.missing_documents == []
        assert result.wrong_documents == []
        lab = next(c for c in result.classifications if c.file_id == "lab")
        assert lab.document_type == DocumentType.LAB_REPORT

    @pytest.mark.anyio
    async def test_genuine_diagnostic_report_mentioning_mri_stays_diagnostic_report_not_forced_to_lab_report(self):
        """Overcorrection guard: a genuinely lab-structure-less diagnostic
        report that happens to describe an MRI must NOT be coerced into
        LAB_REPORT by keyword matching — there is no such logic in the
        agent, only in the (separately tested) prompt guidance, and this
        test locks in that the agent never second-guesses the AI's
        classification based on document content it doesn't have access to."""
        storage = _FakeDocumentStorage({"CLM-1/report.pdf": b"%PDF-diagnostic-report-mentions-mri"})
        provider = _FakeAnalyzeDocumentProvider(
            {"document_type": "DIAGNOSTIC_REPORT", "quality": "GOOD", "patient_name": "", "confidence": 0.85}
        )
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [DocumentMetadata(file_id="report", file_name="mri_report.pdf", mime_type="application/pdf", storage_reference="CLM-1/report.pdf")]

        result = await agent.run(claim_category=ClaimCategory.CONSULTATION, documents=docs, classifications={})

        assert result.classifications[0].document_type == DocumentType.DIAGNOSTIC_REPORT

    @pytest.mark.anyio
    async def test_diagnostic_claim_still_blocked_if_lab_document_misclassified_as_diagnostic_report(self):
        """Documents the exact pre-fix failure mode as a negative control:
        if the AI were to (incorrectly) return DIAGNOSTIC_REPORT for the
        lab document of a DIAGNOSTIC claim, verification correctly reports
        LAB_REPORT missing and DIAGNOSTIC_REPORT wrong — proving the
        set-membership requirement logic itself was always correct, and
        confirming the bug could only ever have been the classification
        input, never this agent's matching logic."""
        storage = _FakeDocumentStorage({
            "CLM-1/rx.pdf": b"%PDF-rx", "CLM-1/lab.pdf": b"%PDF-lab", "CLM-1/bill.pdf": b"%PDF-bill",
        })
        provider = _FakeSequentialAnalyzeDocumentProvider([
            {"document_type": "PRESCRIPTION", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
            {"document_type": "DIAGNOSTIC_REPORT", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
            {"document_type": "HOSPITAL_BILL", "quality": "GOOD", "patient_name": "", "confidence": 0.9},
        ])
        agent = DocumentVerificationAgent(
            ai_provider=provider, policy_repository=PolicyRepository(), document_storage=storage
        )
        docs = [
            DocumentMetadata(file_id="rx", file_name="rx.pdf", mime_type="application/pdf", storage_reference="CLM-1/rx.pdf"),
            DocumentMetadata(file_id="lab", file_name="lab.pdf", mime_type="application/pdf", storage_reference="CLM-1/lab.pdf"),
            DocumentMetadata(file_id="bill", file_name="bill.pdf", mime_type="application/pdf", storage_reference="CLM-1/bill.pdf"),
        ]

        result = await agent.run(claim_category=ClaimCategory.DIAGNOSTIC, documents=docs, classifications={})

        assert result.status == DocumentVerificationStatus.BLOCKED
        assert DocumentType.LAB_REPORT in result.missing_documents
        assert DocumentType.DIAGNOSTIC_REPORT in result.wrong_documents


class _FakeSequentialAnalyzeDocumentProvider:
    """AIProvider double returning pre-set analyze_document responses in
    call order — for claims with multiple documents needing different
    classifications (mirrors _FakeSequentialAIProvider in
    tests/integration/test_claims_api.py, scoped to this file)."""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self._index = 0

    async def analyze_document(self, request):
        data = self._responses[self._index]
        self._index += 1
        return DocumentAnalysisResponse(structured_data=data, model="fake-vision-model", provider="fake")

    async def generate_structured(self, request):  # pragma: no cover
        raise AssertionError("real uploads must use analyze_document")
