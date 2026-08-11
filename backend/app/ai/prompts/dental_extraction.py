"""
Prompt + structured-output schema for dental report/bill extraction.

sample_documents_guide.md has no dedicated dental template, so this schema
is derived from the assignment's own DENTAL requirements
(policy_terms.json's opd_categories.dental — covered vs. excluded
procedures like "Root Canal Treatment" vs. "Teeth Whitening", see TC006)
plus the general hospital-bill shape, since a dental bill is structurally
a specialised hospital bill. Coverage/exclusion matching itself is
PolicyEngine's job (Phase 2B/3), not this schema's.
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

This document is a dental bill or dental report. Extract the patient \
identity, the dentist's identity, the date, the diagnosis and/or \
procedure performed (e.g. "Root Canal Treatment", "Teeth Whitening" — \
extract the procedure name exactly as written; do not judge whether it is \
cosmetic or covered), any treatment notes, and the billed amount."""

DENTAL_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        **base_schema_properties(),
        "patient_name": {"type": "string", "description": "Patient's name as written, or \"\"."},
        "dentist_name": {"type": "string", "description": "Dentist's name, or \"\"."},
        "dentist_registration_number": {"type": "string", "description": "If shown, else \"\"."},
        "dentist_hospital_or_clinic": {"type": "string", "description": "Clinic name, or \"\"."},
        "date_on_document": {"type": "string", "description": "YYYY-MM-DD if shown, else \"\"."},
        "diagnosis": {"type": "string", "description": "Diagnosis as written, or \"\"."},
        "procedure": {"type": "string", "description": "Procedure name exactly as written, or \"\"."},
        "treatment": {"type": "string", "description": "Treatment notes if distinct from procedure, or \"\"."},
        "amount": {"type": "string", "description": "Billed amount as a number string, or \"\"."},
        "notes": {"type": "string", "description": "Any other relevant notes on the document, or \"\"."},
    },
    "required": [
        *BASE_REQUIRED_FIELDS,
        "patient_name", "dentist_name", "dentist_registration_number",
        "dentist_hospital_or_clinic", "date_on_document", "diagnosis",
        "procedure", "treatment", "amount", "notes",
    ],
}


def build_dental_extraction_request(
    *, file_name: Optional[str], content: bytes, media_type: MediaType
) -> DocumentAnalysisRequest:
    return build_extraction_request(
        document_label="dental bill/report",
        system_prompt=SYSTEM_PROMPT,
        schema=DENTAL_EXTRACTION_SCHEMA,
        file_name=file_name,
        content=content,
        media_type=media_type,
        prompt_version=PROMPT_VERSION,
    )
