"""
Prompt + structured-output schema for AI-based document classification.

Used by DocumentVerificationAgent for any document that doesn't already
have a pre-supplied classification (i.e. real submissions — see
DocumentInputAdapter). Kept separate from the agent so the prompt is
versionable and editable without touching orchestration code.

Two request builders, for the two ways a document reaches this stage:
- `build_document_analysis_request` — the real path (Phase 2A correction):
  the actual uploaded file bytes (PDF/JPEG/PNG), sent through the
  provider's native multimodal document-analysis capability
  (`AIProvider.analyze_document`). This is what real uploads use.
- `build_document_classification_request` — a text-only fallback (filename
  + declared type only, via `AIProvider.generate_structured`) for a
  document with no bytes available. Kept for callers that only have
  metadata, not file content — DocumentVerificationAgent only falls back
  to this if a document has no `storage_reference`.
"""

from __future__ import annotations

import base64
from typing import Optional

from app.ai.schemas.ai_schemas import (
    AIStructuredRequest,
    ContentRole,
    DocumentAnalysisRequest,
    DocumentContent,
    ImageContent,
    MediaType,
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


def build_document_analysis_request(
    *,
    file_name: Optional[str],
    content: bytes,
    media_type: MediaType,
) -> DocumentAnalysisRequest:
    """
    Build the real multimodal document-analysis request — the actual file
    bytes, sent through the provider's native document/vision capability.
    This is the primary path for real uploads (Phase 2A correction): the
    AI looks at the real document, not just its filename.

    `file_name` is passed only as supplementary context in the prompt text
    (a hint the model may use, e.g. "prescription_2.jpg" suggesting a
    prescription) — never as the basis for classification on its own. The
    document content is what actually gets classified.
    """
    data = base64.b64encode(content).decode("ascii")
    if media_type == MediaType.PDF:
        doc_content = DocumentContent(media_type=media_type, data=data, filename=file_name)
    else:
        doc_content = ImageContent(media_type=media_type, data=data)

    prompt = (
        "Classify this insurance claim document. "
        f"File name (a hint only, not proof of type): {file_name or '(not provided)'}. "
        "Look at the actual document content to determine its type, quality, and patient name — "
        "do not rely on the filename alone."
    )

    return DocumentAnalysisRequest(
        documents=[doc_content],
        prompt=prompt,
        system_prompt=DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT,
        output_schema=DOCUMENT_CLASSIFICATION_SCHEMA,
        file_ids=[file_name] if file_name else [],
        metadata={"prompt_version": "2a-correction.1"},
    )


def build_document_classification_request(
    *,
    file_name: Optional[str],
    declared_type: Optional[DocumentType] = None,
    hint_text: Optional[str] = None,
) -> AIStructuredRequest:
    """
    Text-only fallback classification request (filename + declared type,
    no document bytes). Used only when a document has no stored content to
    analyze — real uploads always go through `build_document_analysis_request`
    instead. Kept for robustness, not as the primary path.

    Args:
        file_name: The uploaded file's name, if known.
        declared_type: What the member said this document is, if provided.
        hint_text: Any additional extracted text/context to classify from.
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
