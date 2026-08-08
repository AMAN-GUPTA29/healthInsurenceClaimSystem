"""
Prompt + structured-output schema for AI-based document classification.

Used by DocumentVerificationAgent for any document that doesn't already
have a pre-supplied classification (i.e. real submissions — see
DocumentInputAdapter). Kept separate from the agent so the prompt is
versionable and editable without touching orchestration code.

Current scope (Phase 2A): no file upload/OCR pipeline exists yet, so this
builds a *text-only* classification request from the filename and any
member-declared type — a provisional stand-in for real multimodal
classification. When file upload lands, this same function's `hint_text`
parameter is where extracted OCR text or an inline image part would be
added; the schema and downstream parsing do not need to change.
"""

from __future__ import annotations

from typing import Optional

from app.ai.schemas.ai_schemas import (
    AIStructuredRequest,
    ContentRole,
    Message,
    TextContent,
)
from app.domain.models import DocumentQuality, DocumentType

DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT = """\
You are a document classification assistant for a health insurance claims \
system in India. Given information about an uploaded claim document, \
determine its most likely type, how readable/usable it is, and the patient \
name if one is visible in the provided information.

Be conservative: if the available information does not clearly indicate a \
document type, prefer UNKNOWN over guessing. If you cannot determine \
whether the document is readable, prefer a lower confidence score rather \
than assuming it is GOOD quality.

Never invent a patient name — return an empty string if none is evident."""

# Gemini's response_schema accepts an OpenAPI-3.0-like subset, not full JSON
# Schema (see docs/AI_HANDOFF.md Decision 8) — avoid nullable/union types
# here (e.g. ["string","null"]) so the same schema works unmodified against
# both the Gemini and Anthropic adapters. patient_name uses "" for "none
# found" rather than null for this reason.
DOCUMENT_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "enum": [t.value for t in DocumentType],
            "description": "The document's actual type.",
        },
        "quality": {
            "type": "string",
            "enum": [q.value for q in DocumentQuality],
            "description": "How readable/usable the document is.",
        },
        "patient_name": {
            "type": "string",
            "description": "Patient name if evident from the given information, otherwise an empty string.",
        },
        "confidence": {
            "type": "number",
            "description": "Confidence in this classification, from 0.0 to 1.0.",
        },
        "reasoning": {
            "type": "string",
            "description": "One short sentence explaining the classification.",
        },
    },
    "required": ["document_type", "quality", "patient_name", "confidence"],
}


def build_document_classification_request(
    *,
    file_name: Optional[str],
    declared_type: Optional[DocumentType] = None,
    hint_text: Optional[str] = None,
) -> AIStructuredRequest:
    """
    Build the structured-generation request for classifying one document.

    Args:
        file_name: The uploaded file's name, if known.
        declared_type: What the member said this document is, if provided.
        hint_text: Any additional extracted text/context to classify from
            (OCR text, image description, etc. — not used yet in Phase 2A).
    """
    lines = ["Classify this insurance claim document."]
    lines.append(f"File name: {file_name or '(not provided)'}")
    if declared_type:
        lines.append(f"Member-declared type: {declared_type.value}")
    if hint_text:
        lines.append(f"Additional context: {hint_text}")
    lines.append(
        "Respond with the document's actual type, its readability/quality, "
        "and the patient name if evident."
    )

    return AIStructuredRequest(
        messages=[Message(role=ContentRole.USER, content=[TextContent(text="\n".join(lines))])],
        output_schema=DOCUMENT_CLASSIFICATION_SCHEMA,
        schema_name="document_classification",
        system_prompt=DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT,
        metadata={"prompt_version": "2a.1"},
    )
