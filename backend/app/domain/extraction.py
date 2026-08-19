"""
Document extraction domain models — Phase 2B.

Answers a narrower question than DocumentVerificationAgent: not "is this
the right document and is it usable?" (Phase 2A, app/domain/verification.py)
but "what information does this specific document actually contain?".

Rules (same as the rest of app/domain/):
- Pure Pydantic models. No database imports. No FastAPI imports. No AI SDK imports.
- All monetary amounts are Decimal — never float.
- Every field the AI could not find is None (or an empty list), never a
  guessed value — the AI-facing schemas in app/ai/prompts/*_extraction.py
  use explicit sentinel values ("" / [] ) for "not visible", which the
  `_empty_to_none`/`_to_decimal` validators below convert to None. Nothing
  here decides whether a claim is covered or how much is payable — that is
  PolicyEngine/FinancialCalculationService's job (Phase 2B/3, not this file).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

from app.domain.models import DocumentQuality, DocumentType
from app.domain.trace import AITraceMetadata

# ── Shared value-parsing helpers ────────────────────────────────────────────
#
# The AI-facing JSON schemas (app/ai/prompts/*_extraction.py) never use
# `null` — Gemini's response_schema is an OpenAPI-3.0-like subset that
# doesn't reliably support nullable unions, so every "not visible" value
# comes back as a sentinel: "" for strings/
# dates/amounts, [] for lists, and the literal string "UNCLEAR" for the
# tri-state signature/stamp/abnormal-flag fields. These validators translate
# those sentinels into real None/Decimal/bool values for the domain layer —
# the AI-facing shape and the domain shape are deliberately different.


def _empty_to_none(v: Any) -> Any:
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def _to_decimal(v: Any) -> Optional[Decimal]:
    v = _empty_to_none(v)
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        cleaned = str(v).replace(",", "").replace("₹", "").strip()
        if cleaned == "":
            return None
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _to_tristate_bool(v: Any) -> Optional[bool]:
    v = _empty_to_none(v)
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    normalized = str(v).strip().upper()
    if normalized == "YES":
        return True
    if normalized == "NO":
        return False
    return None  # "UNCLEAR" or anything unrecognised


# ── Shared nested value objects ─────────────────────────────────────────────


class PatientInfo(BaseModel):
    """Envelope-level patient identity. Individual extraction schemas below
    populate only the subset of these fields their document type can
    realistically show (see each schema's docstring)."""

    name: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    date_of_birth: Optional[date] = None
    member_id: Optional[str] = None

    _clean_strs = field_validator("name", "gender", "member_id", mode="before")(_empty_to_none)

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _clean_dob(cls, v: Any) -> Any:
        return _empty_to_none(v)

    @field_validator("age", mode="before")
    @classmethod
    def _clean_age(cls, v: Any) -> Any:
        v = _empty_to_none(v)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None


class DoctorInfo(BaseModel):
    name: Optional[str] = None
    registration_number: Optional[str] = None
    specialization: Optional[str] = None
    hospital_or_clinic: Optional[str] = None

    _clean = field_validator(
        "name", "registration_number", "specialization", "hospital_or_clinic", mode="before"
    )(_empty_to_none)


class EvidenceItem(BaseModel):
    """A short verbatim quote supporting one extracted field — kept for a
    handful of clinically/financially important fields only (diagnosis,
    treatment, total amount, ...), not every field. See
    COMPONENT_CONTRACTS.docx "6. Document Extraction" for why this is a
    bounded list rather than a {value, evidence} wrapper on every field."""

    field: str
    quote: str


class Medication(BaseModel):
    name: str
    strength: Optional[str] = None
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    duration: Optional[str] = None
    route: Optional[str] = None
    instructions: Optional[str] = None

    _clean = field_validator(
        "strength", "dosage", "frequency", "duration", "route", "instructions", mode="before"
    )(_empty_to_none)


class LineItem(BaseModel):
    description: str
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    amount: Optional[Decimal] = None

    _clean_amounts = field_validator("quantity", "unit_price", "amount", mode="before")(_to_decimal)


class LabTestResult(BaseModel):
    test_name: str
    result: Optional[str] = None
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    abnormal_flag: Optional[bool] = None

    _clean_strs = field_validator("result", "unit", "reference_range", mode="before")(_empty_to_none)
    _clean_flag = field_validator("abnormal_flag", mode="before")(_to_tristate_bool)


class PharmacyItem(BaseModel):
    medicine_name: str
    batch_number: Optional[str] = None
    expiry_date: Optional[str] = None  # free text — most bills show MM/YY, not a full date
    quantity: Optional[Decimal] = None
    mrp: Optional[Decimal] = None
    discount: Optional[Decimal] = None
    amount: Optional[Decimal] = None

    _clean_strs = field_validator("batch_number", "expiry_date", mode="before")(_empty_to_none)
    _clean_amounts = field_validator("quantity", "mrp", "discount", "amount", mode="before")(_to_decimal)


# ── Shared base for the six per-document-type schemas ───────────────────────


class ExtractionBase(BaseModel):
    """
    Fields every per-document-type extraction schema carries, regardless of
    document type: the AI's own confidence in this extraction, any warnings
    it flagged (unreadable/ambiguous/missing fields — see
    app/ai/prompts/extraction_common.py), and a short list of verbatim
    quotes backing the most important extracted fields.

    `confidence` is required and AI-supplied — never fabricated in Python
    (mirrors the same rule DocumentVerificationResult/TraceEvent.confidence
    already follow).
    """

    confidence: float = Field(ge=0.0, le=1.0)
    warnings: List[str] = Field(default_factory=list)
    evidence: List[EvidenceItem] = Field(default_factory=list)


# ── Per-document-type extraction schemas ────────────────────────────────────


class PrescriptionExtraction(ExtractionBase):
    """Doctor's Rx — see sample_documents_guide.md 'Medical Prescription'."""

    document_type: Literal[DocumentType.PRESCRIPTION] = DocumentType.PRESCRIPTION
    patient: PatientInfo = Field(default_factory=PatientInfo)
    prescription_date: Optional[date] = None
    doctor: DoctorInfo = Field(default_factory=DoctorInfo)
    diagnosis: Optional[str] = None
    treatment: Optional[str] = None
    medications: List[Medication] = Field(default_factory=list)
    investigations: List[str] = Field(default_factory=list)
    signature_present: Optional[bool] = None
    stamp_present: Optional[bool] = None

    _clean_strs = field_validator("diagnosis", "treatment", mode="before")(_empty_to_none)
    _clean_date = field_validator("prescription_date", mode="before")(_empty_to_none)
    _clean_flags = field_validator("signature_present", "stamp_present", mode="before")(_to_tristate_bool)


class HospitalBillExtraction(ExtractionBase):
    """Hospital bill / clinic invoice — see sample_documents_guide.md."""

    document_type: Literal[DocumentType.HOSPITAL_BILL] = DocumentType.HOSPITAL_BILL
    patient_name: Optional[str] = None
    hospital_name: Optional[str] = None
    bill_number: Optional[str] = None
    bill_date: Optional[date] = None
    admission_date: Optional[date] = None
    discharge_date: Optional[date] = None
    doctor: DoctorInfo = Field(default_factory=DoctorInfo)
    line_items: List[LineItem] = Field(default_factory=list)
    subtotal: Optional[Decimal] = None
    discount: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    total: Optional[Decimal] = None
    currency: str = "INR"

    _clean_strs = field_validator("patient_name", "hospital_name", "bill_number", mode="before")(
        _empty_to_none
    )
    _clean_dates = field_validator("bill_date", "admission_date", "discharge_date", mode="before")(
        _empty_to_none
    )
    _clean_amounts = field_validator("subtotal", "discount", "tax", "total", mode="before")(_to_decimal)


class LabReportExtraction(ExtractionBase):
    """Diagnostic / lab report — see sample_documents_guide.md."""

    document_type: Literal[DocumentType.LAB_REPORT] = DocumentType.LAB_REPORT
    patient: PatientInfo = Field(default_factory=PatientInfo)
    referring_doctor: Optional[str] = None
    sample_date: Optional[date] = None
    report_date: Optional[date] = None
    tests: List[LabTestResult] = Field(default_factory=list)
    laboratory_name: Optional[str] = None
    pathologist_name: Optional[str] = None
    registration_number: Optional[str] = None

    _clean_strs = field_validator(
        "referring_doctor", "laboratory_name", "pathologist_name", "registration_number", mode="before"
    )(_empty_to_none)
    _clean_dates = field_validator("sample_date", "report_date", mode="before")(_empty_to_none)


class PharmacyBillExtraction(ExtractionBase):
    """Pharmacy bill — see sample_documents_guide.md."""

    document_type: Literal[DocumentType.PHARMACY_BILL] = DocumentType.PHARMACY_BILL
    patient_name: Optional[str] = None
    pharmacy_name: Optional[str] = None
    bill_number: Optional[str] = None
    bill_date: Optional[date] = None
    items: List[PharmacyItem] = Field(default_factory=list)
    subtotal: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    total: Optional[Decimal] = None

    _clean_strs = field_validator("patient_name", "pharmacy_name", "bill_number", mode="before")(
        _empty_to_none
    )
    _clean_dates = field_validator("bill_date", mode="before")(_empty_to_none)
    _clean_amounts = field_validator("subtotal", "tax", "total", mode="before")(_to_decimal)


class DentalReportExtraction(ExtractionBase):
    """Dental bill/report — covered vs. cosmetic procedures matter for
    policy evaluation later (see policy_terms.json's dental_exclusions),
    but this schema only extracts what's on the document; it does not
    decide coverage."""

    document_type: Literal[DocumentType.DENTAL_REPORT] = DocumentType.DENTAL_REPORT
    patient_name: Optional[str] = None
    dentist: DoctorInfo = Field(default_factory=DoctorInfo)
    date_on_document: Optional[date] = None
    diagnosis: Optional[str] = None
    procedure: Optional[str] = None
    treatment: Optional[str] = None
    amount: Optional[Decimal] = None
    notes: Optional[str] = None

    _clean_strs = field_validator("patient_name", "diagnosis", "procedure", "treatment", "notes", mode="before")(
        _empty_to_none
    )
    _clean_date = field_validator("date_on_document", mode="before")(_empty_to_none)
    _clean_amount = field_validator("amount", mode="before")(_to_decimal)


class DischargeSummaryExtraction(ExtractionBase):
    """Hospital discharge summary."""

    document_type: Literal[DocumentType.DISCHARGE_SUMMARY] = DocumentType.DISCHARGE_SUMMARY
    patient_name: Optional[str] = None
    hospital_name: Optional[str] = None
    admission_date: Optional[date] = None
    discharge_date: Optional[date] = None
    diagnosis: Optional[str] = None
    procedure_or_treatment: Optional[str] = None
    doctor: DoctorInfo = Field(default_factory=DoctorInfo)
    discharge_instructions: Optional[str] = None
    total_amount: Optional[Decimal] = None

    _clean_strs = field_validator(
        "patient_name", "hospital_name", "diagnosis", "procedure_or_treatment", "discharge_instructions",
        mode="before",
    )(_empty_to_none)
    _clean_dates = field_validator("admission_date", "discharge_date", mode="before")(_empty_to_none)
    _clean_amount = field_validator("total_amount", mode="before")(_to_decimal)


ExtractionPayload = Annotated[
    Union[
        PrescriptionExtraction,
        HospitalBillExtraction,
        LabReportExtraction,
        PharmacyBillExtraction,
        DentalReportExtraction,
        DischargeSummaryExtraction,
    ],
    Field(discriminator="document_type"),
]

# Only these types have an extraction schema in Phase 2B. DIAGNOSTIC_REPORT,
# PRE_AUTH_LETTER, and UNKNOWN are classified (Phase 2A) but not extracted —
# DocumentExtractionAgent records them as `skipped`, never a failure.
SUPPORTED_EXTRACTION_TYPES: Dict[DocumentType, type] = {
    DocumentType.PRESCRIPTION: PrescriptionExtraction,
    DocumentType.HOSPITAL_BILL: HospitalBillExtraction,
    DocumentType.LAB_REPORT: LabReportExtraction,
    DocumentType.PHARMACY_BILL: PharmacyBillExtraction,
    DocumentType.DENTAL_REPORT: DentalReportExtraction,
    DocumentType.DISCHARGE_SUMMARY: DischargeSummaryExtraction,
}


# ── Common envelope ──────────────────────────────────────────────────────────


class DocumentExtractionResult(BaseModel):
    """
    The common envelope for one successfully extracted document — what the
    API/frontend/persistence layer actually consumes. `extraction` carries
    the document-type-specific payload (confidence/warnings/evidence live
    there too, via ExtractionBase, so they round-trip as a single JSON blob
    at persistence time — see app/repositories/claim_models.py).
    """

    file_id: str
    document_type: DocumentType
    quality: DocumentQuality
    patient: PatientInfo = Field(default_factory=PatientInfo)
    document_date: Optional[date] = None
    source: str = "ai"  # "ai" only in Phase 2B — no fixture path for extraction, see agent docstring
    extraction: ExtractionPayload

    @property
    def confidence(self) -> float:
        return self.extraction.confidence

    @property
    def warnings(self) -> List[str]:
        return self.extraction.warnings


class DocumentExtractionFailure(BaseModel):
    """
    Recorded when one document's extraction could not be completed. The
    claim and every other document must still proceed — see
    DocumentExtractionAgent's docstring and COMPONENT_CONTRACTS.docx
    "6. Document Extraction" for why this is deliberately per-document,
    not per-claim.
    """

    file_id: str
    document_type: Optional[DocumentType] = None
    reason: str


class ClaimExtractionResult(BaseModel):
    """Output of DocumentExtractionAgent — aggregates every document's
    extraction attempt for one claim. Mirrors DocumentVerificationResult's
    shape (a single result object per stage, per claim)."""

    extractions: List[DocumentExtractionResult] = Field(default_factory=list)
    failures: List[DocumentExtractionFailure] = Field(default_factory=list)
    skipped: List[str] = Field(
        default_factory=list,
        description="file_ids not attempted — document type has no extraction schema "
        "(e.g. DIAGNOSTIC_REPORT, UNKNOWN), not a failure.",
    )
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    has_failures: bool = False
    ai_calls: List[AITraceMetadata] = Field(default_factory=list)
