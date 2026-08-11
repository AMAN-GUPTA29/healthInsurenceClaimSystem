"""
AI Provider Schemas.

These are the typed request/response contracts between the AI provider interface
and its callers. They are completely vendor-agnostic — no provider SDK types
appear here.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


# ── Capability Enumerations ───────────────────────────────────────────────────


class ContentRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MediaType(str, Enum):
    """Supported media MIME types for multimodal inputs."""

    JPEG = "image/jpeg"
    PNG = "image/png"
    PDF = "application/pdf"
    WEBP = "image/webp"
    GIF = "image/gif"


# ── Message Types ─────────────────────────────────────────────────────────────


class TextContent(BaseModel):
    type: str = "text"
    text: str


class ImageContent(BaseModel):
    """
    Inline base64-encoded image or a URL reference.
    Providers that don't support URL references must fetch and encode themselves.
    """

    type: str = "image"
    media_type: MediaType
    data: Optional[str] = None       # base64-encoded bytes
    url: Optional[str] = None        # Public URL (not all providers support this)

    model_config = {"populate_by_name": True}


class DocumentContent(BaseModel):
    """
    A document (PDF) for multimodal analysis.
    Providers that support native PDF processing use `data` directly.
    Others must convert pages to images first.
    """

    type: str = "document"
    media_type: MediaType = MediaType.PDF
    data: str  # base64-encoded PDF bytes
    filename: Optional[str] = None


MessageContent = Union[TextContent, ImageContent, DocumentContent]


class Message(BaseModel):
    role: ContentRole
    content: Union[str, List[MessageContent]]


# ── Requests ──────────────────────────────────────────────────────────────────


class AIGenerateRequest(BaseModel):
    """
    Request to generate a text response from the AI provider.
    Use for open-ended generation, summaries, explanations.
    """

    messages: List[Message]
    system_prompt: Optional[str] = None
    model: Optional[str] = None           # Override the default model for this request
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout_seconds: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AIStructuredRequest(BaseModel):
    """
    Request to generate a structured JSON response conforming to a schema.
    Use for extraction, classification, and any step needing typed output.
    """

    messages: List[Message]
    output_schema: Dict[str, Any]         # JSON Schema describing the expected output
    schema_name: str = "response"
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout_seconds: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DocumentAnalysisRequest(BaseModel):
    """
    Request to analyse one or more medical documents (images/PDFs).
    Use for document verification, type detection, and data extraction.
    """

    documents: List[Union[ImageContent, DocumentContent]]
    prompt: str                           # Specific instruction for this analysis
    system_prompt: Optional[str] = None
    output_schema: Optional[Dict[str, Any]] = None  # If set, returns structured output
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout_seconds: Optional[int] = None
    file_ids: List[str] = Field(default_factory=list)  # Tracing: which files are in this call
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Responses ─────────────────────────────────────────────────────────────────


class TokenUsage(BaseModel):
    """Token consumption metadata returned by the provider where available."""

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None


class AIGenerateResponse(BaseModel):
    """Response from a text generation request."""

    text: str
    model: str
    provider: str
    usage: Optional[TokenUsage] = None
    finish_reason: Optional[str] = None
    latency_ms: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AIStructuredResponse(BaseModel):
    """Response from a structured generation request."""

    data: Dict[str, Any]        # Parsed structured output
    raw_text: Optional[str] = None
    model: str
    provider: str
    usage: Optional[TokenUsage] = None
    finish_reason: Optional[str] = None
    latency_ms: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DocumentAnalysisResponse(BaseModel):
    """Response from a document analysis request."""

    text: Optional[str] = None
    structured_data: Optional[Dict[str, Any]] = None
    model: str
    provider: str
    usage: Optional[TokenUsage] = None
    finish_reason: Optional[str] = None
    latency_ms: Optional[float] = None
    file_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
