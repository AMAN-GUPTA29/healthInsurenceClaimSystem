"""
Trace domain models — the vocabulary every pipeline component uses to report
what it did, so a claim's decision can be reconstructed and explained later.

Rules (same as app/domain/models.py):
- Pure Pydantic models. No database imports. No FastAPI imports. No AI SDK imports.
- No component may invent its own free-form event/status strings — use
  TraceComponent / TraceEventType.
- Never store secrets (API keys, auth headers, env vars) in a TraceEvent.
  `redact_metadata` strips anything that looks like one before it reaches
  a TraceEvent — see app/tracing/service.py, which calls it on every event.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from app.domain.errors import ClaimsSystemError

# ── Component Vocabulary ──────────────────────────────────────────────────────


class TraceComponent(str, Enum):
    """
    Identifies which pipeline component produced a trace event.

    Central, typed vocabulary so future agents never invent ad-hoc string
    identifiers scattered across the codebase.
    """

    CLAIM_VALIDATION = "CLAIM_VALIDATION"
    DOCUMENT_VERIFICATION = "DOCUMENT_VERIFICATION"
    DOCUMENT_EXTRACTION = "DOCUMENT_EXTRACTION"
    CROSS_DOCUMENT_VALIDATION = "CROSS_DOCUMENT_VALIDATION"
    POLICY_ENGINE = "POLICY_ENGINE"
    FRAUD_ANALYSIS = "FRAUD_ANALYSIS"
    FINANCIAL_CALCULATION = "FINANCIAL_CALCULATION"
    DECISION_GENERATION = "DECISION_GENERATION"
    EXPLANATION = "EXPLANATION"
    PIPELINE = "PIPELINE"


class TraceEventType(str, Enum):
    """Controlled status/event vocabulary for trace events."""

    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    WARNING = "WARNING"


# ── Supporting Structures ─────────────────────────────────────────────────────


class TraceErrorInfo(BaseModel):
    """
    Safe, structured error information attached to a FAILED trace event.

    Deliberately narrow: only what's needed to explain a decision later.
    Never carries stack traces, request payloads, or exception args that
    might contain secrets — see `error_info_from_exception`.
    """

    error_type: str
    code: Optional[str] = None
    message: str
    recoverable: bool = False


class AITraceMetadata(BaseModel):
    """
    Optional AI call metadata a component may attach to a trace event.

    All fields are optional because not every provider/call reports every
    value (e.g. some providers don't return token counts). Never include
    prompts, responses, or API keys here — only call metadata.
    """

    provider: Optional[str] = None
    model: Optional[str] = None
    latency_ms: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


# ── Trace Event ────────────────────────────────────────────────────────────────


class TraceEvent(BaseModel):
    """
    A single structured trace event.

    Design notes:
    - `metadata` is for small, structured, summarized facts (e.g.
      {"document_type": "PRESCRIPTION", "quality": "GOOD"}), never full
      documents, prompts, or AI responses — those belong in separate
      storage if ever needed. See app/tracing/service.py `redact_metadata`.
    - `confidence` is optional; components that don't naturally produce a
      confidence score must leave it unset rather than inventing a value.
    - `sequence` is populated only when an event is read back from
      persistence (see TraceRepository) — it reflects insertion order and
      is the authoritative ordering key, since two events can share a
      timestamp at sub-millisecond pipeline speed.
    """

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str
    claim_id: str
    component: TraceComponent
    event_type: TraceEventType
    message: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: Optional[float] = Field(default=None, ge=0.0)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[TraceErrorInfo] = None
    ai_metadata: Optional[AITraceMetadata] = None
    sequence: Optional[int] = None


# ── Trace Context ──────────────────────────────────────────────────────────────


class TraceContext(BaseModel):
    """
    Carries trace_id + claim_id through the pipeline so individual
    components never have to thread those two values through every method
    signature by hand. Pass it explicitly (constructor injection) — never
    stash it in global/module state.

    Usage:
        context = TraceContext.new(claim_id=claim.claim_id)
        tracer = TraceService(context)
    """

    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    claim_id: str

    @classmethod
    def new(cls, claim_id: str) -> "TraceContext":
        """Create a fresh trace context (new trace_id) for a claim."""
        return cls(claim_id=claim_id)


# ── Error Translation ─────────────────────────────────────────────────────────


def error_info_from_exception(exc: BaseException) -> TraceErrorInfo:
    """
    Translate any exception into safe, structured TraceErrorInfo.

    Preserves the existing ClaimsSystemError.recoverable semantic — the
    orchestrator's "stop vs continue" decision is not affected by tracing.
    Never includes exception args, stack traces, or arbitrary attributes,
    since those could carry sensitive request data.
    """
    if isinstance(exc, ClaimsSystemError):
        return TraceErrorInfo(
            error_type=type(exc).__name__,
            code=exc.code,
            message=exc.message,
            recoverable=exc.recoverable,
        )
    return TraceErrorInfo(
        error_type=type(exc).__name__,
        code=None,
        message=str(exc),
        recoverable=False,
    )
