"""
TraceService — the reusable tracing infrastructure every pipeline component
uses to report what it did.

Design:
- Takes a TraceContext (trace_id + claim_id) via constructor injection —
  no hidden global mutable state.
- Optionally takes a `sink` (anything with `async def record(event)`,
  e.g. TraceRepository) for persistence. Without a sink, events are still
  collected in-memory and retrievable via `.events` — this keeps the
  service usable in unit tests with zero database setup.
- `span()` is the primary ergonomic API: it emits STARTED on entry and
  COMPLETED/FAILED on exit, capturing duration automatically.
- The explicit `started` / `completed` / `failed` / `warning` / `skipped`
  methods exist for components that need to report confidence, AI
  metadata, or a SKIPPED status that `span()` can't express on its own.

Usage:
    context = TraceContext.new(claim_id=claim.claim_id)
    tracer = TraceService(context, sink=trace_repository)

    async with tracer.span(TraceComponent.DOCUMENT_VERIFICATION):
        ...  # STARTED emitted on entry, COMPLETED/FAILED on exit

    await tracer.completed(
        TraceComponent.DOCUMENT_EXTRACTION,
        confidence=0.93,
        metadata={"document_type": "PRESCRIPTION", "quality": "GOOD"},
    )
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, runtime_checkable

from app.domain.trace import (
    AITraceMetadata,
    TraceComponent,
    TraceContext,
    TraceErrorInfo,
    TraceEvent,
    TraceEventType,
    error_info_from_exception,
)
from app.tracing.logging import get_logger

logger = get_logger("TraceService")

# Keys that must never surface in trace metadata. Matched as a case-insensitive
# substring of the metadata key — deliberately narrow so legitimate business
# fields (e.g. "policy_rule", "primary_member_id") are never accidentally
# redacted.
_SENSITIVE_KEY_MARKERS = ("api_key", "apikey", "secret", "token", "password", "authorization")
_REDACTED = "[REDACTED]"


def redact_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strip anything that looks like a secret out of a metadata dict before it
    reaches a TraceEvent. Never trust component-supplied metadata blindly.
    """
    redacted: Dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = key.lower()
        if any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS):
            redacted[key] = _REDACTED
        else:
            redacted[key] = value
    return redacted


@runtime_checkable
class TraceSink(Protocol):
    """Anything that can durably record a TraceEvent (e.g. TraceRepository)."""

    async def record(self, event: TraceEvent) -> None: ...


class TraceService:
    """Reusable tracing façade injected into pipeline components."""

    def __init__(self, context: TraceContext, *, sink: Optional[TraceSink] = None) -> None:
        self._context = context
        self._sink = sink
        self._events: List[TraceEvent] = []

    @property
    def context(self) -> TraceContext:
        return self._context

    @property
    def events(self) -> List[TraceEvent]:
        """All events recorded through this service instance, in emission order."""
        return list(self._events)

    # ── Explicit event recording ────────────────────────────────────────────

    async def started(
        self,
        component: TraceComponent,
        message: str = "",
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        return await self._emit(component, TraceEventType.STARTED, message=message, metadata=metadata)

    async def completed(
        self,
        component: TraceComponent,
        *,
        message: str = "",
        duration_ms: Optional[float] = None,
        confidence: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ai_metadata: Optional[AITraceMetadata] = None,
    ) -> TraceEvent:
        return await self._emit(
            component,
            TraceEventType.COMPLETED,
            message=message,
            duration_ms=duration_ms,
            confidence=confidence,
            metadata=metadata,
            ai_metadata=ai_metadata,
        )

    async def failed(
        self,
        component: TraceComponent,
        error: BaseException,
        *,
        message: str = "",
        duration_ms: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        error_info: TraceErrorInfo = error_info_from_exception(error)
        return await self._emit(
            component,
            TraceEventType.FAILED,
            message=message or error_info.message,
            duration_ms=duration_ms,
            metadata=metadata,
            error=error_info,
        )

    async def warning(
        self,
        component: TraceComponent,
        message: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        return await self._emit(component, TraceEventType.WARNING, message=message, metadata=metadata)

    async def skipped(
        self,
        component: TraceComponent,
        message: str = "",
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        return await self._emit(component, TraceEventType.SKIPPED, message=message, metadata=metadata)

    # ── Span — automatic STARTED/COMPLETED/FAILED with duration ────────────

    @asynccontextmanager
    async def span(
        self,
        component: TraceComponent,
        *,
        message: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[None]:
        """
        Emit STARTED on entry; COMPLETED (or FAILED, re-raising) on exit,
        with duration_ms measured automatically.

        Does not support confidence/ai_metadata on the COMPLETED event —
        use the explicit `completed()` call after the span if a component
        needs to report those.
        """
        await self.started(component, message=message, metadata=metadata)
        t0 = time.monotonic()
        try:
            yield
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            await self.failed(component, exc, duration_ms=duration_ms)
            raise
        else:
            duration_ms = (time.monotonic() - t0) * 1000
            await self.completed(component, duration_ms=duration_ms)

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _emit(
        self,
        component: TraceComponent,
        event_type: TraceEventType,
        *,
        message: str = "",
        duration_ms: Optional[float] = None,
        confidence: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[TraceErrorInfo] = None,
        ai_metadata: Optional[AITraceMetadata] = None,
    ) -> TraceEvent:
        event = TraceEvent(
            trace_id=self._context.trace_id,
            claim_id=self._context.claim_id,
            component=component,
            event_type=event_type,
            message=message,
            duration_ms=duration_ms,
            confidence=confidence,
            metadata=redact_metadata(metadata or {}),
            error=error,
            ai_metadata=ai_metadata,
        )
        self._events.append(event)
        self._log(event)
        if self._sink is not None:
            await self._sink.record(event)
        return event

    def _log(self, event: TraceEvent) -> None:
        log_fn = {
            TraceEventType.FAILED: logger.error,
            TraceEventType.WARNING: logger.warning,
        }.get(event.event_type, logger.info)
        log_fn(
            f"[{event.component.value}] {event.event_type.value} — {event.message}",
            claim_id=event.claim_id,
            trace_id=event.trace_id,
        )


def create_trace_service(claim_id: str, *, persist: bool = True) -> TraceService:
    """
    Convenience factory for pipeline code (Phase 2+): builds a fresh
    TraceContext for the given claim and wires up a TraceRepository as the
    persistence sink by default.

    Set persist=False to get an in-memory-only tracer (e.g. for a dry-run
    or a unit test that doesn't want database side effects).
    """
    from app.repositories.trace_repository import TraceRepository

    context = TraceContext.new(claim_id=claim_id)
    sink = TraceRepository() if persist else None
    return TraceService(context, sink=sink)
