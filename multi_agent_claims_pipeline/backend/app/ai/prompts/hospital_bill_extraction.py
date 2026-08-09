"""
Prompt + structured-output schema for hospital bill / clinic invoice
extraction. Field list follows sample_documents_guide.md's "Hospital Bill /
Clinic Invoice" section.
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

This document is a hospital bill or clinic invoice. Extract the patient \
identity, hospital/clinic identity, bill number/date, admission/discharge \
dates if this is an inpatient bill, the treating doctor if named, every \
line item with its amount, and the bill's subtotal/discount/tax/total.

Indian clinic bills are frequently small/handwritten with no GSTIN, and \
line items are sometimes vague (e.g. "Medicines" instead of an itemised \
list) — extract exactly what is itemised; do not split a vague line into \
invented sub-items."""

HOSPITAL_BILL_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        **base_schema_properties(),
        "patient_name": {"type": "string", "description": "Patient's name as written, or \"\"."},
        "hospital_name": {"type": "string", "description": "Hospital/clinic name, or \"\"."},
        "bill_number": {"type": "string", "description": "Bill/receipt number, or \"\"."},
        "bill_date": {"type": "string", "description": "YYYY-MM-DD if shown, else \"\"."},
        "admission_date": {"type": "string", "description": "YYYY-MM-DD if this is an inpatient bill, else \"\"."},
        "discharge_date": {"type": "string", "description": "YYYY-MM-DD if this is an inpatient bill, else \"\"."},
        "doctor_name": {"type": "string", "description": "Treating/referring doctor's name, or \"\"."},
        "doctor_registration_number": {"type": "string", "description": "If shown, else \"\"."},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Line item description as written."},
                    "quantity": {"type": "string", "description": "Quantity as a number string, or \"\"."},
                    "unit_price": {"type": "string", "description": "Unit price as a number string, or \"\"."},
                    "amount": {"type": "string", "description": "Line amount as a number string, or \"\"."},
                },
                "required": ["description", "quantity", "unit_price", "amount"],
            },
            "description": "Every billed line item. Empty array if none itemised.",
        },
        "subtotal": {"type": "string", "description": "Subtotal before tax/discount, as a number string, or \"\"."},
        "discount": {"type": "string", "description": "Discount amount, as a number string, or \"\"."},
        "tax": {"type": "string", "description": "GST/tax amount, as a number string, or \"\"."},
        "total": {"type": "string", "description": "Final total amount, as a number string, or \"\"."},
        "currency": {"type": "string", "description": "Currency code, default 'INR' if not stated otherwise."},
    },
    "required": [
        *BASE_REQUIRED_FIELDS,
        "patient_name", "hospital_name", "bill_number", "bill_date", "admission_date",
        "discharge_date", "doctor_name", "doctor_registration_number", "line_items",
        "subtotal", "discount", "tax", "total", "currency",
    ],
}


def build_hospital_bill_extraction_request(
    *, file_name: Optional[str], content: bytes, media_type: MediaType
) -> DocumentAnalysisRequest:
    return build_extraction_request(
        document_label="hospital bill",
        system_prompt=SYSTEM_PROMPT,
        schema=HOSPITAL_BILL_EXTRACTION_SCHEMA,
        file_name=file_name,
        content=content,
        media_type=media_type,
        prompt_version=PROMPT_VERSION,
    )
