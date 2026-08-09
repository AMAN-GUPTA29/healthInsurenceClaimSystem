"""
Shared building blocks for the six document-extraction prompt modules
(prescription_extraction.py, hospital_bill_extraction.py, etc.).

Kept separate so the extraction rules that must apply to every document
type (never hallucinate, preserve uncertainty, ...) are defined once, while
each per-document-type module stays independently editable/versionable —
see docs/AI_HANDOFF.md Phase 2B "Modular Prompts".

Schema design note: every schema built on top of `EXTRACTION_BASE_SCHEMA`
avoids `null`/nullable unions and `$ref`/`oneOf`/`allOf` — Gemini's
`response_schema` only accepts an OpenAPI-3.0-like subset (see Decision 8
in docs/AI_HANDOFF.md). "Not visible" is represented with an explicit
sentinel instead: "" for strings/dates/amounts, [] for lists, "UNCLEAR" for
the tri-state yes/no/unclear fields. app/domain/extraction.py's validators
convert these sentinels into real None/Decimal/bool values — the AI-facing
shape and the domain shape are deliberately different.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List

from app.ai.schemas.ai_schemas import DocumentAnalysisRequest, DocumentContent, ImageContent, MediaType

EXTRACTION_RULES_TEXT = """\
You are a medical document data-extraction assistant for a health insurance \
claims system in India. You will be shown one real uploaded document \
(image or PDF). Extract only the structured fields you are asked for.

Rules — follow all of these exactly:
1. Never hallucinate. Never invent a value that is not visible in the document.
2. Never infer information that is not directly supported by what you can see.
3. If a field is not present or not legible, return "" (empty string) for \
text/date/amount fields, [] (empty array) for lists, or "UNCLEAR" for the \
yes/no/unclear fields — never guess, never leave the field out entirely.
4. Preserve uncertainty: if a value is only partially legible, extract your \
best reading of it AND add a warning describing exactly what is uncertain \
(e.g. "Doctor registration number could not be read clearly.").
5. Extract exact visible values — do not normalise medicine names, correct \
apparent spelling mistakes, or round amounts.
6. Dates: always output as YYYY-MM-DD if you can determine the exact date; \
otherwise "".
7. Monetary amounts: output the digits and decimal point exactly as \
written (e.g. "1500.00"), with no currency symbol or thousands separators, \
as a string; otherwise "".
8. Do not calculate, total, or validate any amount — copy what is on the \
document. Do not calculate or state any policy/coverage/claim decision.
9. Do not decide whether this claim should be approved. That is not your job.
10. Do not invent a patient identity or member ID that is not visible on \
this specific document.
11. Do not trust the file name as evidence of what the document contains — \
classify and extract only from the actual visual content.
12. Set `confidence` (0.0-1.0) to reflect how confident you are in this \
extraction overall, based on the document's actual legibility — a blurry \
or partially-cut-off document should get a lower confidence score, not the \
same score as a clean scan.
13. In `evidence`, include 2-5 short verbatim quotes from the document that \
support the most important fields you extracted (e.g. the diagnosis line, \
the total amount line) — do not paraphrase, quote the actual text."""


def evidence_schema_property() -> Dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "Which extracted field this quote supports, e.g. 'diagnosis'."},
                "quote": {"type": "string", "description": "A short verbatim quote from the document."},
            },
            "required": ["field", "quote"],
        },
        "description": "2-5 short verbatim quotes backing the most important extracted fields.",
    }


def base_schema_properties() -> Dict[str, Any]:
    """Properties every extraction schema includes — merge into each
    document-type schema's own `properties` dict."""
    return {
        "confidence": {
            "type": "number",
            "description": "Confidence in this extraction overall, from 0.0 to 1.0, based on document legibility.",
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific warnings about unreadable, partially visible, ambiguous, or missing fields.",
        },
        "evidence": evidence_schema_property(),
    }


BASE_REQUIRED_FIELDS: List[str] = ["confidence", "warnings", "evidence"]


def build_extraction_request(
    *,
    document_label: str,
    system_prompt: str,
    schema: Dict[str, Any],
    file_name: str | None,
    content: bytes,
    media_type: MediaType,
    prompt_version: str,
) -> DocumentAnalysisRequest:
    """
    Shared request builder for all six extraction prompt modules — wraps
    the real uploaded bytes (never re-derives type from the filename) and
    attaches the document-type-specific system prompt/schema built by the
    caller. Mirrors build_document_analysis_request in
    document_verification.py (Phase 2A), generalised to a dynamic
    prompt/schema instead of one hardcoded to classification.
    """
    data = base64.b64encode(content).decode("ascii")
    if media_type == MediaType.PDF:
        doc_content = DocumentContent(media_type=media_type, data=data, filename=file_name)
    else:
        doc_content = ImageContent(media_type=media_type, data=data)

    prompt = (
        f"Extract structured data from this {document_label}. "
        f"File name (context only, not evidence of content): {file_name or '(not provided)'}. "
        "Extract only what is visible in the actual document content."
    )

    return DocumentAnalysisRequest(
        documents=[doc_content],
        prompt=prompt,
        system_prompt=system_prompt,
        output_schema=schema,
        file_ids=[file_name] if file_name else [],
        metadata={"prompt_version": prompt_version},
    )
