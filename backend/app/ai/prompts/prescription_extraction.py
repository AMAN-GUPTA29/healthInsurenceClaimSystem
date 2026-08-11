"""
Prompt + structured-output schema for prescription (doctor's Rx) extraction.

Field list follows sample_documents_guide.md's "Medical Prescription"
section: doctor identity, patient identity, diagnosis, medicines with
dosage/duration, tests ordered, and document indicators (signature/stamp)
mentioned there as real-world variations ("Rubber stamps over text").
"""

from __future__ import annotations

from typing import Optional

from app.ai.prompts.extraction_common import (
    BASE_REQUIRED_FIELDS,
    EXTRACTION_RULES_TEXT,
    base_schema_properties,
    build_extraction_request,
)
from app.ai.schemas.ai_schemas import DocumentAnalysisRequest, MediaType

PROMPT_VERSION = "2b.1"

SYSTEM_PROMPT = f"""\
{EXTRACTION_RULES_TEXT}

This document is a doctor's prescription (Rx). Extract the patient's \
identity as printed/written on this prescription, the prescribing \
doctor's identity, the diagnosis, the prescribed medications (with \
dosage/frequency/duration where legible), any investigations/tests \
ordered, and whether a signature and/or stamp are visible.

Indian prescriptions are frequently handwritten or partially illegible, \
mix regional-language text with English medicine names, and may have a \
rubber stamp over the registration number — extract what you can read and \
use `warnings` for anything you could not."""

TRISTATE_ENUM = {"type": "string", "enum": ["YES", "NO", "UNCLEAR"]}

PRESCRIPTION_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        **base_schema_properties(),
        "patient_name": {"type": "string", "description": "Patient's name as written, or \"\"."},
        "patient_age": {"type": "string", "description": "Patient's age as written (digits only), or \"\"."},
        "patient_gender": {"type": "string", "description": "Patient's gender as written, or \"\"."},
        "patient_date_of_birth": {"type": "string", "description": "YYYY-MM-DD if shown, else \"\"."},
        "prescription_date": {"type": "string", "description": "YYYY-MM-DD if shown, else \"\"."},
        "doctor_name": {"type": "string", "description": "Prescribing doctor's name, or \"\"."},
        "doctor_registration_number": {"type": "string", "description": "e.g. 'KA/45678/2015', or \"\"."},
        "doctor_specialization": {"type": "string", "description": "e.g. 'MBBS, MD (Internal Medicine)', or \"\"."},
        "doctor_hospital_or_clinic": {"type": "string", "description": "Clinic/hospital name, or \"\"."},
        "diagnosis": {"type": "string", "description": "Diagnosis as written (may use shorthand like HTN/T2DM), or \"\"."},
        "treatment": {"type": "string", "description": "Named treatment/therapy if distinct from a medicine list, or \"\"."},
        "medications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Medicine name as written."},
                    "strength": {"type": "string", "description": "e.g. '650mg', or \"\"."},
                    "dosage": {"type": "string", "description": "e.g. '1-1-1', or \"\"."},
                    "frequency": {"type": "string", "description": "e.g. 'twice daily', or \"\"."},
                    "duration": {"type": "string", "description": "e.g. '5 days', or \"\"."},
                    "route": {"type": "string", "description": "e.g. 'oral', or \"\"."},
                    "instructions": {"type": "string", "description": "e.g. 'after food', or \"\"."},
                },
                "required": ["name", "strength", "dosage", "frequency", "duration", "route", "instructions"],
            },
            "description": "Every medicine line on the prescription. Empty array if none legible.",
        },
        "investigations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tests/investigations ordered (e.g. 'CBC', 'Dengue NS1'). Empty array if none.",
        },
        "signature_present": {**TRISTATE_ENUM, "description": "Is a doctor's signature visible?"},
        "stamp_present": {**TRISTATE_ENUM, "description": "Is a clinic/registration stamp visible?"},
    },
    "required": [
        *BASE_REQUIRED_FIELDS,
        "patient_name", "patient_age", "patient_gender", "patient_date_of_birth",
        "prescription_date", "doctor_name", "doctor_registration_number",
        "doctor_specialization", "doctor_hospital_or_clinic", "diagnosis", "treatment",
        "medications", "investigations", "signature_present", "stamp_present",
    ],
}


def build_prescription_extraction_request(
    *, file_name: Optional[str], content: bytes, media_type: MediaType
) -> DocumentAnalysisRequest:
    return build_extraction_request(
        document_label="prescription",
        system_prompt=SYSTEM_PROMPT,
        schema=PRESCRIPTION_EXTRACTION_SCHEMA,
        file_name=file_name,
        content=content,
        media_type=media_type,
        prompt_version=PROMPT_VERSION,
    )
