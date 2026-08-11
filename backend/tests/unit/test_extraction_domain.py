"""
Unit tests for app/domain/extraction.py — the six typed extraction schemas,
their sentinel-value parsing ("" / [] / "UNCLEAR" -> None, Decimal parsing),
the discriminated union, and malformed-input rejection.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.extraction import (
    ClaimExtractionResult,
    DentalReportExtraction,
    DischargeSummaryExtraction,
    DoctorInfo,
    DocumentExtractionFailure,
    DocumentExtractionResult,
    HospitalBillExtraction,
    LabReportExtraction,
    PatientInfo,
    PharmacyBillExtraction,
    PrescriptionExtraction,
)
from app.domain.models import DocumentQuality, DocumentType


class TestPrescriptionExtraction:
    def test_complete_extraction(self):
        p = PrescriptionExtraction(
            patient=PatientInfo(name="Rajesh Kumar", age=39, gender="M", date_of_birth=date(1985, 3, 15)),
            prescription_date=date(2024, 11, 1),
            doctor=DoctorInfo(name="Dr. Arun Sharma", registration_number="KA/45678/2015"),
            diagnosis="Viral Fever",
            medications=[{"name": "Paracetamol", "strength": "650mg", "dosage": "1-1-1"}],
            investigations=["CBC", "Dengue NS1"],
            signature_present=True,
            stamp_present=True,
            confidence=0.94,
            warnings=[],
            evidence=[{"field": "diagnosis", "quote": "Diagnosis: Viral Fever"}],
        )
        assert p.patient.name == "Rajesh Kumar"
        assert p.medications[0].strength == "650mg"
        assert p.confidence == 0.94
        assert p.evidence[0].field == "diagnosis"

    def test_missing_optional_fields_become_none(self):
        """Sentinel values ("" / [] / "UNCLEAR") from the AI-facing schema
        parse to None/empty, never a fabricated value."""
        p = PrescriptionExtraction.model_validate(
            {
                "patient": {"name": "", "age": "", "gender": "", "date_of_birth": ""},
                "prescription_date": "",
                "doctor": {"name": "", "registration_number": "", "specialization": "", "hospital_or_clinic": ""},
                "diagnosis": "",
                "treatment": "",
                "medications": [],
                "investigations": [],
                "signature_present": "UNCLEAR",
                "stamp_present": "UNCLEAR",
                "confidence": 0.3,
                "warnings": ["Document is partially cut off."],
                "evidence": [],
            }
        )
        assert p.patient.name is None
        assert p.patient.age is None
        assert p.prescription_date is None
        assert p.doctor.name is None
        assert p.diagnosis is None
        assert p.signature_present is None
        assert p.stamp_present is None
        assert p.warnings == ["Document is partially cut off."]

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            PrescriptionExtraction(confidence=1.5)

    def test_confidence_is_required(self):
        with pytest.raises(ValidationError):
            PrescriptionExtraction.model_validate({})


class TestHospitalBillExtraction:
    def test_line_items_and_amounts_parse_to_decimal(self):
        bill = HospitalBillExtraction.model_validate(
            {
                "patient_name": "Rajesh Kumar",
                "hospital_name": "City Clinic",
                "bill_number": "CMC/2024/08321",
                "bill_date": "2024-11-01",
                "admission_date": "",
                "discharge_date": "",
                "doctor": {"name": "Dr. Arun Sharma", "registration_number": ""},
                "line_items": [
                    {"description": "Consultation Fee", "quantity": "1", "unit_price": "1000.00", "amount": "1000.00"},
                    {"description": "CBC Test", "quantity": "1", "unit_price": "300.00", "amount": "300.00"},
                ],
                "subtotal": "1300.00",
                "discount": "",
                "tax": "0.00",
                "total": "1300.00",
                "currency": "INR",
                "confidence": 0.9,
                "warnings": [],
                "evidence": [],
            }
        )
        assert bill.total == Decimal("1300.00")
        assert bill.discount is None
        assert bill.line_items[0].amount == Decimal("1000.00")
        assert isinstance(bill.line_items[0].amount, Decimal)

    def test_amount_with_currency_symbol_and_commas_parses(self):
        bill = HospitalBillExtraction(total="₹1,500.00", confidence=0.9)
        assert bill.total == Decimal("1500.00")

    def test_unparseable_amount_becomes_none_not_an_error(self):
        bill = HospitalBillExtraction(total="not a number", confidence=0.9)
        assert bill.total is None

    def test_document_type_is_fixed(self):
        bill = HospitalBillExtraction(confidence=0.9)
        assert bill.document_type == DocumentType.HOSPITAL_BILL


class TestLabReportExtraction:
    def test_multiple_tests_with_abnormal_flags(self):
        report = LabReportExtraction.model_validate(
            {
                "patient": {"name": "Rajesh Kumar", "age": "39", "gender": "M"},
                "referring_doctor": "Dr. Arun Sharma",
                "sample_date": "2024-11-01",
                "report_date": "2024-11-01",
                "tests": [
                    {"test_name": "Hemoglobin", "result": "13.2", "unit": "g/dL", "reference_range": "13.0-17.0", "abnormal_flag": "NO"},
                    {"test_name": "WBC Count", "result": "9800", "unit": "/uL", "reference_range": "4500-11000", "abnormal_flag": "UNCLEAR"},
                ],
                "laboratory_name": "Precision Diagnostics",
                "pathologist_name": "Dr. Meena Pillai",
                "registration_number": "KA/89012/2018",
                "confidence": 0.88,
                "warnings": [],
                "evidence": [],
            }
        )
        assert len(report.tests) == 2
        assert report.tests[0].abnormal_flag is False
        assert report.tests[1].abnormal_flag is None  # UNCLEAR -> None, never guessed

    def test_missing_reference_range_is_none(self):
        report = LabReportExtraction(
            tests=[{"test_name": "Dengue NS1", "result": "NEGATIVE", "reference_range": ""}],
            confidence=0.9,
        )
        assert report.tests[0].reference_range is None


class TestPharmacyBillExtraction:
    def test_multiple_medicines_with_quantities_and_amounts(self):
        bill = PharmacyBillExtraction.model_validate(
            {
                "patient_name": "Rajesh Kumar",
                "pharmacy_name": "Health First Pharmacy",
                "bill_number": "HFP-24-09821",
                "bill_date": "2024-11-01",
                "items": [
                    {"medicine_name": "Paracetamol 650", "batch_number": "A2341", "expiry_date": "03/26", "quantity": "15", "mrp": "2.50", "discount": "", "amount": "37.50"},
                    {"medicine_name": "Vitamin C 500", "batch_number": "B7821", "expiry_date": "06/26", "quantity": "10", "mrp": "4.00", "discount": "", "amount": "40.00"},
                ],
                "subtotal": "77.50",
                "tax": "",
                "total": "73.62",
                "confidence": 0.85,
                "warnings": [],
                "evidence": [],
            }
        )
        assert len(bill.items) == 2
        assert bill.items[0].quantity == Decimal("15")
        assert bill.items[0].expiry_date == "03/26"
        assert bill.total == Decimal("73.62")


class TestDentalAndDischargeExtraction:
    def test_dental_extraction(self):
        dental = DentalReportExtraction(
            patient_name="Priya Singh",
            dentist=DoctorInfo(name="Dr. S. Rao"),
            procedure="Root Canal Treatment",
            amount="8000.00",
            confidence=0.9,
        )
        assert dental.amount == Decimal("8000.00")
        assert dental.document_type == DocumentType.DENTAL_REPORT

    def test_discharge_summary_extraction(self):
        discharge = DischargeSummaryExtraction(
            patient_name="Vikram Joshi",
            hospital_name="City Hospital",
            admission_date="2024-11-01",
            discharge_date="2024-11-03",
            diagnosis="Acute Bronchitis",
            confidence=0.9,
        )
        assert discharge.admission_date == date(2024, 11, 1)
        assert discharge.document_type == DocumentType.DISCHARGE_SUMMARY


class TestDiscriminatedUnionEnvelope:
    def test_envelope_round_trips_through_json_by_document_type(self):
        envelope = DocumentExtractionResult(
            file_id="F007",
            document_type=DocumentType.PRESCRIPTION,
            quality=DocumentQuality.GOOD,
            patient=PatientInfo(name="Rajesh Kumar"),
            document_date=date(2024, 11, 1),
            extraction=PrescriptionExtraction(diagnosis="Viral Fever", confidence=0.94),
        )
        dumped = envelope.model_dump(mode="json")
        rehydrated = DocumentExtractionResult.model_validate(dumped)

        assert isinstance(rehydrated.extraction, PrescriptionExtraction)
        assert rehydrated.extraction.diagnosis == "Viral Fever"
        assert rehydrated.confidence == 0.94  # property delegates to .extraction.confidence
        assert rehydrated.warnings == []

    def test_envelope_selects_hospital_bill_type_from_discriminator(self):
        envelope = DocumentExtractionResult(
            file_id="F008",
            document_type=DocumentType.HOSPITAL_BILL,
            quality=DocumentQuality.GOOD,
            extraction=HospitalBillExtraction(total="1500.00", confidence=0.9),
        )
        dumped = envelope.model_dump(mode="json")
        rehydrated = DocumentExtractionResult.model_validate(dumped)
        assert isinstance(rehydrated.extraction, HospitalBillExtraction)
        assert rehydrated.extraction.total == Decimal("1500.00")

    def test_malformed_extraction_payload_rejected(self):
        with pytest.raises(ValidationError):
            DocumentExtractionResult(
                file_id="F1",
                document_type=DocumentType.PRESCRIPTION,
                quality=DocumentQuality.GOOD,
                extraction={"document_type": "PRESCRIPTION"},  # missing required `confidence`
            )


class TestClaimExtractionResult:
    def test_aggregates_extractions_failures_and_skipped(self):
        result = ClaimExtractionResult(
            extractions=[
                DocumentExtractionResult(
                    file_id="F1",
                    document_type=DocumentType.PRESCRIPTION,
                    quality=DocumentQuality.GOOD,
                    extraction=PrescriptionExtraction(confidence=0.9),
                )
            ],
            failures=[DocumentExtractionFailure(file_id="F2", reason="AI timeout")],
            skipped=["F3"],
            confidence=0.9,
            has_failures=True,
        )
        assert len(result.extractions) == 1
        assert result.failures[0].file_id == "F2"
        assert result.skipped == ["F3"]
        assert result.has_failures is True
