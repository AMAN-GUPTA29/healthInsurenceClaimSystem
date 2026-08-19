"""
DocumentExtractionAgent — Phase 2B pipeline stage, runs after cross-document
validation.

Answers a narrower question than DocumentVerificationAgent: not "is this
the correct document and is it usable?" (Phase 2A) but "what information is
actually contained in this document?". Kept as a separate agent so
DocumentVerificationAgent never grows into a general extraction agent — see
COMPONENT_CONTRACTS.docx "6. Document Extraction" for the full rationale.

Always uses real AI calls (AIProvider.analyze_document, the same
multimodal capability DocumentVerificationAgent uses for real uploads) —
there is deliberately no fixture/text-only extraction path the way
DocumentVerificationAgent has one for evaluation fixtures. A document with
no stored bytes cannot be extracted at all and is recorded as a
per-document failure, not silently skipped as if
it succeeded. Fixtures/mocks are only used at the AIProvider boundary in
automated unit tests, per the Phase 2B brief.

Failure isolation: one document's extraction failing (AI timeout, parse
error, no stored bytes) never stops the others — see `run()`'s per-document
try/except. This mirrors ClaimsPipeline's own graceful-degradation
guarantee (Decision 18) but scoped to a single stage: `run()` itself never
raises for a per-document problem; the aggregate ClaimExtractionResult
reports `has_failures` and a reduced `confidence` instead.
"""

from __future__ import annotations

from datetime import date
from typing import Callable, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.agents.base_agent import BaseAgent
from app.ai.prompts.dental_extraction import build_dental_extraction_request
from app.ai.prompts.discharge_summary_extraction import build_discharge_summary_extraction_request
from app.ai.prompts.hospital_bill_extraction import build_hospital_bill_extraction_request
from app.ai.prompts.lab_report_extraction import build_lab_report_extraction_request
from app.ai.prompts.pharmacy_bill_extraction import build_pharmacy_bill_extraction_request
from app.ai.prompts.prescription_extraction import build_prescription_extraction_request
from app.ai.schemas.ai_schemas import DocumentAnalysisRequest, MediaType
from app.domain.errors import ExtractionError
from app.domain.extraction import (
    SUPPORTED_EXTRACTION_TYPES,
    ClaimExtractionResult,
    DentalReportExtraction,
    DischargeSummaryExtraction,
    DoctorInfo,
    DocumentExtractionFailure,
    DocumentExtractionResult,
    ExtractionPayload,
    HospitalBillExtraction,
    LabReportExtraction,
    PatientInfo,
    PharmacyBillExtraction,
    PrescriptionExtraction,
)
from app.domain.models import DocumentMetadata, DocumentType
from app.domain.trace import AITraceMetadata
from app.storage.document_storage import DocumentStorage

# ── Per-document-type dispatch ──────────────────────────────────────────────
#
# Maps a document's classified type to (a) the request builder for its
# prompt/schema (app/ai/prompts/*_extraction.py) and (b) the adapter that
# turns the AI's raw dict into the typed extraction model + this envelope's
# top-level patient/document_date convenience fields. Adding a seventh
# document type means adding one entry here and one prompt module — nothing
# else in this agent changes.

_RequestBuilder = Callable[..., DocumentAnalysisRequest]
_Adapter = Callable[[dict], Tuple[ExtractionPayload, PatientInfo, Optional[date]]]


def _adapt_prescription(data: dict) -> Tuple[ExtractionPayload, PatientInfo, Optional[date]]:
    extraction = PrescriptionExtraction(
        patient=PatientInfo(
            name=data["patient_name"],
            age=data["patient_age"],
            gender=data["patient_gender"],
            date_of_birth=data["patient_date_of_birth"],
        ),
        prescription_date=data["prescription_date"],
        doctor=DoctorInfo(
            name=data["doctor_name"],
            registration_number=data["doctor_registration_number"],
            specialization=data["doctor_specialization"],
            hospital_or_clinic=data["doctor_hospital_or_clinic"],
        ),
        diagnosis=data["diagnosis"],
        treatment=data["treatment"],
        medications=data["medications"],
        investigations=data["investigations"],
        signature_present=data["signature_present"],
        stamp_present=data["stamp_present"],
        confidence=data["confidence"],
        warnings=data["warnings"],
        evidence=data["evidence"],
    )
    return extraction, extraction.patient, extraction.prescription_date


def _adapt_hospital_bill(data: dict) -> Tuple[ExtractionPayload, PatientInfo, Optional[date]]:
    extraction = HospitalBillExtraction(
        patient_name=data["patient_name"],
        hospital_name=data["hospital_name"],
        bill_number=data["bill_number"],
        bill_date=data["bill_date"],
        admission_date=data["admission_date"],
        discharge_date=data["discharge_date"],
        doctor=DoctorInfo(name=data["doctor_name"], registration_number=data["doctor_registration_number"]),
        line_items=data["line_items"],
        subtotal=data["subtotal"],
        discount=data["discount"],
        tax=data["tax"],
        total=data["total"],
        currency=data["currency"] or "INR",
        confidence=data["confidence"],
        warnings=data["warnings"],
        evidence=data["evidence"],
    )
    return extraction, PatientInfo(name=extraction.patient_name), extraction.bill_date


def _adapt_lab_report(data: dict) -> Tuple[ExtractionPayload, PatientInfo, Optional[date]]:
    extraction = LabReportExtraction(
        patient=PatientInfo(name=data["patient_name"], age=data["patient_age"], gender=data["patient_gender"]),
        referring_doctor=data["referring_doctor"],
        sample_date=data["sample_date"],
        report_date=data["report_date"],
        tests=data["tests"],
        laboratory_name=data["laboratory_name"],
        pathologist_name=data["pathologist_name"],
        registration_number=data["registration_number"],
        confidence=data["confidence"],
        warnings=data["warnings"],
        evidence=data["evidence"],
    )
    return extraction, extraction.patient, (extraction.report_date or extraction.sample_date)


def _adapt_pharmacy_bill(data: dict) -> Tuple[ExtractionPayload, PatientInfo, Optional[date]]:
    extraction = PharmacyBillExtraction(
        patient_name=data["patient_name"],
        pharmacy_name=data["pharmacy_name"],
        bill_number=data["bill_number"],
        bill_date=data["bill_date"],
        items=data["items"],
        subtotal=data["subtotal"],
        tax=data["tax"],
        total=data["total"],
        confidence=data["confidence"],
        warnings=data["warnings"],
        evidence=data["evidence"],
    )
    return extraction, PatientInfo(name=extraction.patient_name), extraction.bill_date


def _adapt_dental(data: dict) -> Tuple[ExtractionPayload, PatientInfo, Optional[date]]:
    extraction = DentalReportExtraction(
        patient_name=data["patient_name"],
        dentist=DoctorInfo(
            name=data["dentist_name"],
            registration_number=data["dentist_registration_number"],
            hospital_or_clinic=data["dentist_hospital_or_clinic"],
        ),
        date_on_document=data["date_on_document"],
        diagnosis=data["diagnosis"],
        procedure=data["procedure"],
        treatment=data["treatment"],
        amount=data["amount"],
        notes=data["notes"],
        confidence=data["confidence"],
        warnings=data["warnings"],
        evidence=data["evidence"],
    )
    return extraction, PatientInfo(name=extraction.patient_name), extraction.date_on_document


def _adapt_discharge_summary(data: dict) -> Tuple[ExtractionPayload, PatientInfo, Optional[date]]:
    extraction = DischargeSummaryExtraction(
        patient_name=data["patient_name"],
        hospital_name=data["hospital_name"],
        admission_date=data["admission_date"],
        discharge_date=data["discharge_date"],
        diagnosis=data["diagnosis"],
        procedure_or_treatment=data["procedure_or_treatment"],
        doctor=DoctorInfo(name=data["doctor_name"], registration_number=data["doctor_registration_number"]),
        discharge_instructions=data["discharge_instructions"],
        total_amount=data["total_amount"],
        confidence=data["confidence"],
        warnings=data["warnings"],
        evidence=data["evidence"],
    )
    return extraction, PatientInfo(name=extraction.patient_name), (extraction.discharge_date or extraction.admission_date)


_REQUEST_BUILDERS: Dict[DocumentType, _RequestBuilder] = {
    DocumentType.PRESCRIPTION: build_prescription_extraction_request,
    DocumentType.HOSPITAL_BILL: build_hospital_bill_extraction_request,
    DocumentType.LAB_REPORT: build_lab_report_extraction_request,
    DocumentType.PHARMACY_BILL: build_pharmacy_bill_extraction_request,
    DocumentType.DENTAL_REPORT: build_dental_extraction_request,
    DocumentType.DISCHARGE_SUMMARY: build_discharge_summary_extraction_request,
}

_ADAPTERS: Dict[DocumentType, _Adapter] = {
    DocumentType.PRESCRIPTION: _adapt_prescription,
    DocumentType.HOSPITAL_BILL: _adapt_hospital_bill,
    DocumentType.LAB_REPORT: _adapt_lab_report,
    DocumentType.PHARMACY_BILL: _adapt_pharmacy_bill,
    DocumentType.DENTAL_REPORT: _adapt_dental,
    DocumentType.DISCHARGE_SUMMARY: _adapt_discharge_summary,
}

assert set(_REQUEST_BUILDERS) == set(_ADAPTERS) == set(SUPPORTED_EXTRACTION_TYPES)


def _media_type_for(mime_type: Optional[str]) -> MediaType:
    normalized = (mime_type or "").lower()
    if normalized == "application/pdf":
        return MediaType.PDF
    if normalized == "image/png":
        return MediaType.PNG
    return MediaType.JPEG


class DocumentExtractionAgent(BaseAgent):
    def __init__(
        self,
        *,
        ai_provider,
        document_storage: DocumentStorage,
        agent_name: Optional[str] = None,
    ) -> None:
        super().__init__(ai_provider=ai_provider, agent_name=agent_name)
        self._document_storage = document_storage

    async def run(self, *, documents: List[DocumentMetadata]) -> ClaimExtractionResult:
        extractions: List[DocumentExtractionResult] = []
        failures: List[DocumentExtractionFailure] = []
        skipped: List[str] = []
        ai_calls: List[AITraceMetadata] = []

        for doc in documents:
            doc_type = doc.detected_type or doc.declared_type
            if doc_type not in SUPPORTED_EXTRACTION_TYPES:
                skipped.append(doc.file_id)
                continue

            try:
                envelope, ai_metadata = await self._extract_one(doc, doc_type)
            except Exception as exc:  # noqa: BLE001 — one bad document must not sink the claim
                failures.append(
                    DocumentExtractionFailure(
                        file_id=doc.file_id,
                        document_type=doc_type,
                        reason=str(exc),
                    )
                )
                continue

            extractions.append(envelope)
            ai_calls.append(ai_metadata)

        confidences = [e.confidence for e in extractions]
        overall_confidence = min(confidences) if confidences else None

        return ClaimExtractionResult(
            extractions=extractions,
            failures=failures,
            skipped=skipped,
            confidence=overall_confidence,
            has_failures=len(failures) > 0,
            ai_calls=ai_calls,
        )

    async def _extract_one(
        self, doc: DocumentMetadata, doc_type: DocumentType
    ) -> Tuple[DocumentExtractionResult, AITraceMetadata]:
        if doc.storage_reference is None:
            raise ExtractionError(doc.file_id, "no stored document content available to extract from")

        content = await self._document_storage.read(doc.storage_reference)
        media_type = _media_type_for(doc.mime_type)
        build_request = _REQUEST_BUILDERS[doc_type]
        request = build_request(file_name=doc.file_name, content=content, media_type=media_type)

        response = await self.ai_provider.analyze_document(request)
        ai_metadata = AITraceMetadata(
            provider=response.provider,
            model=response.model,
            latency_ms=response.latency_ms,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
        )
        if not response.structured_data:
            raise ExtractionError(doc.file_id, "AI provider returned no structured extraction")

        try:
            extraction, patient, document_date = _ADAPTERS[doc_type](response.structured_data)
        except (KeyError, ValidationError) as exc:
            raise ExtractionError(doc.file_id, f"could not parse AI extraction: {exc}") from exc

        envelope = DocumentExtractionResult(
            file_id=doc.file_id,
            document_type=doc_type,
            quality=doc.quality,
            patient=patient,
            document_date=document_date,
            source="ai",
            extraction=extraction,
        )
        return envelope, ai_metadata
