"""
Prompt + structured-output schema for diagnostic / lab report extraction.
Field list follows sample_documents_guide.md's "Diagnostic / Lab Report"
section.
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

This document is a diagnostic/lab report. Extract the patient identity, \
the referring doctor, the sample/report dates, every individual test \
result with its unit and normal reference range, and the laboratory's \
identity including the reporting pathologist.

Flag a test's `abnormal_flag` as YES only if the report itself marks it \
abnormal (e.g. an asterisk, bold text, or explicit "H"/"L"/"Abnormal" \
marker) — do not decide abnormality yourself by comparing the result to \
the reference range."""

LAB_REPORT_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        **base_schema_properties(),
        "patient_name": {"type": "string", "description": "Patient's name as written, or \"\"."},
        "patient_age": {"type": "string", "description": "Patient's age as written (digits only), or \"\"."},
        "patient_gender": {"type": "string", "description": "Patient's gender as written, or \"\"."},
        "referring_doctor": {"type": "string", "description": "Referring doctor's name, or \"\"."},
        "sample_date": {"type": "string", "description": "YYYY-MM-DD if shown, else \"\"."},
        "report_date": {"type": "string", "description": "YYYY-MM-DD if shown, else \"\"."},
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "test_name": {"type": "string", "description": "Test name as written."},
                    "result": {"type": "string", "description": "Result value as written, or \"\"."},
                    "unit": {"type": "string", "description": "Unit as written, or \"\"."},
                    "reference_range": {"type": "string", "description": "Normal range as written, or \"\"."},
                    "abnormal_flag": {"type": "string", "enum": ["YES", "NO", "UNCLEAR"]},
                },
                "required": ["test_name", "result", "unit", "reference_range", "abnormal_flag"],
            },
            "description": "Every individual test result on the report. Empty array if none legible.",
        },
        "laboratory_name": {"type": "string", "description": "Lab name, or \"\"."},
        "pathologist_name": {"type": "string", "description": "Reporting pathologist's name, or \"\"."},
        "registration_number": {"type": "string", "description": "Pathologist's registration number, or \"\"."},
    },
    "required": [
        *BASE_REQUIRED_FIELDS,
        "patient_name", "patient_age", "patient_gender", "referring_doctor",
        "sample_date", "report_date", "tests", "laboratory_name",
        "pathologist_name", "registration_number",
    ],
}


def build_lab_report_extraction_request(
    *, file_name: Optional[str], content: bytes, media_type: MediaType
) -> DocumentAnalysisRequest:
    return build_extraction_request(
        document_label="lab report",
        system_prompt=SYSTEM_PROMPT,
        schema=LAB_REPORT_EXTRACTION_SCHEMA,
        file_name=file_name,
        content=content,
        media_type=media_type,
        prompt_version=PROMPT_VERSION,
    )
