"""
Prompt + structured-output schema for pharmacy bill extraction. Field list
follows sample_documents_guide.md's "Pharmacy Bill" section.

Deliberately does not compute or flag generic-vs-branded medicine policy
logic (policy_terms.json's `generic_mandatory`/`branded_drug_copay_percent`)
— that belongs to PolicyEngine (Phase 2B/3), not this extraction schema.
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

This document is a pharmacy bill. Extract the patient identity, the \
pharmacy's identity, the bill number/date, every medicine line with its \
batch number, expiry, quantity, MRP, discount and amount, and the bill's \
subtotal/tax/total.

Do not decide whether any medicine is generic or branded, and do not \
compute or validate any policy copay — only extract what is printed."""

PHARMACY_BILL_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        **base_schema_properties(),
        "patient_name": {"type": "string", "description": "Patient's name as written, or \"\"."},
        "pharmacy_name": {"type": "string", "description": "Pharmacy name, or \"\"."},
        "bill_number": {"type": "string", "description": "Bill number, or \"\"."},
        "bill_date": {"type": "string", "description": "YYYY-MM-DD if shown, else \"\"."},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "medicine_name": {"type": "string", "description": "Medicine name as written."},
                    "batch_number": {"type": "string", "description": "Batch number, or \"\"."},
                    "expiry_date": {"type": "string", "description": "Expiry as written (often MM/YY), or \"\"."},
                    "quantity": {"type": "string", "description": "Quantity as a number string, or \"\"."},
                    "mrp": {"type": "string", "description": "MRP per unit as a number string, or \"\"."},
                    "discount": {"type": "string", "description": "Discount amount as a number string, or \"\"."},
                    "amount": {"type": "string", "description": "Line amount as a number string, or \"\"."},
                },
                "required": ["medicine_name", "batch_number", "expiry_date", "quantity", "mrp", "discount", "amount"],
            },
            "description": "Every medicine line billed. Empty array if none legible.",
        },
        "subtotal": {"type": "string", "description": "Subtotal, as a number string, or \"\"."},
        "tax": {"type": "string", "description": "Tax amount, as a number string, or \"\"."},
        "total": {"type": "string", "description": "Net/total amount, as a number string, or \"\"."},
    },
    "required": [
        *BASE_REQUIRED_FIELDS,
        "patient_name", "pharmacy_name", "bill_number", "bill_date", "items",
        "subtotal", "tax", "total",
    ],
}


def build_pharmacy_bill_extraction_request(
    *, file_name: Optional[str], content: bytes, media_type: MediaType
) -> DocumentAnalysisRequest:
    return build_extraction_request(
        document_label="pharmacy bill",
        system_prompt=SYSTEM_PROMPT,
        schema=PHARMACY_BILL_EXTRACTION_SCHEMA,
        file_name=file_name,
        content=content,
        media_type=media_type,
        prompt_version=PROMPT_VERSION,
    )
