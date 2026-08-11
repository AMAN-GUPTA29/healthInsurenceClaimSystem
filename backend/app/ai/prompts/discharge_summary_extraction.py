"""
Prompt + structured-output schema for hospital discharge summary extraction.

sample_documents_guide.md has no dedicated discharge-summary template;
fields here follow the assignment's general extraction ask (patient
details, diagnosis, treatment, amounts, dates, doctor details) applied to
what a discharge summary specifically contains: admission/discharge dates,
the treatment/procedure performed during the stay, and discharge
instructions.
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

This document is a hospital discharge summary. Extract the patient \
identity, the hospital's identity, the admission and discharge dates, the \
diagnosis, the procedure/treatment given during the stay, the discharging \
doctor, any discharge instructions given to the patient, and the total \
amount if explicitly stated on this document."""

DISCHARGE_SUMMARY_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        **base_schema_properties(),
        "patient_name": {"type": "string", "description": "Patient's name as written, or \"\"."},
        "hospital_name": {"type": "string", "description": "Hospital name, or \"\"."},
        "admission_date": {"type": "string", "description": "YYYY-MM-DD if shown, else \"\"."},
        "discharge_date": {"type": "string", "description": "YYYY-MM-DD if shown, else \"\"."},
        "diagnosis": {"type": "string", "description": "Diagnosis as written, or \"\"."},
        "procedure_or_treatment": {"type": "string", "description": "Procedure/treatment given, or \"\"."},
        "doctor_name": {"type": "string", "description": "Discharging/treating doctor's name, or \"\"."},
        "doctor_registration_number": {"type": "string", "description": "If shown, else \"\"."},
        "discharge_instructions": {"type": "string", "description": "Discharge instructions, or \"\"."},
        "total_amount": {"type": "string", "description": "Total amount if explicitly stated here, as a number string, or \"\"."},
    },
    "required": [
        *BASE_REQUIRED_FIELDS,
        "patient_name", "hospital_name", "admission_date", "discharge_date",
        "diagnosis", "procedure_or_treatment", "doctor_name",
        "doctor_registration_number", "discharge_instructions", "total_amount",
    ],
}


def build_discharge_summary_extraction_request(
    *, file_name: Optional[str], content: bytes, media_type: MediaType
) -> DocumentAnalysisRequest:
    return build_extraction_request(
        document_label="discharge summary",
        system_prompt=SYSTEM_PROMPT,
        schema=DISCHARGE_SUMMARY_EXTRACTION_SCHEMA,
        file_name=file_name,
        content=content,
        media_type=media_type,
        prompt_version=PROMPT_VERSION,
    )
