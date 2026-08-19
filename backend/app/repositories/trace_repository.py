"""
TraceRepository — persistence for trace events.

All database access goes through this repository; no other layer issues
SQL or touches TraceEventORM directly. Each method opens its own session
via app.repositories.database.get_session(), consistent with the rest of
the Phase 0 database foundation.

Deliberately not shaped as BaseRepository[T, ID]: trace events are a
one-claim-to-many-events relationship, not single-entity CRUD-by-id, so
forcing the generic get_by_id/save/delete shape would fit poorly. See
COMPONENT_CONTRACTS.docx "14. Trace Service" for the rationale.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import select

from app.domain.trace import (
    AITraceMetadata,
    TraceComponent,
    TraceErrorInfo,
    TraceEvent,
    TraceEventType,
)
from app.repositories.database import get_session
from app.repositories.trace_models import TraceEventORM


class TraceRepository:
    """Async persistence + retrieval for TraceEvent records."""

    async def create_event(self, event: TraceEvent) -> None:
        """Persist a single trace event."""
        row = TraceEventORM(
            event_id=event.event_id,
            trace_id=event.trace_id,
            claim_id=event.claim_id,
            component=event.component.value,
            event_type=event.event_type.value,
            message=event.message,
            timestamp=event.timestamp,
            duration_ms=event.duration_ms,
            confidence=event.confidence,
            metadata_json=event.metadata,
            error_json=event.error.model_dump() if event.error else None,
            ai_metadata_json=event.ai_metadata.model_dump() if event.ai_metadata else None,
        )
        async with get_session() as session:
            session.add(row)

    # Alias so a TraceRepository instance structurally satisfies the
    # TraceService `TraceSink` protocol (`async def record(event)`)
    # without needing a separate adapter class.
    record = create_event

    async def list_by_trace_id(self, trace_id: str) -> List[TraceEvent]:
        """All events for a single trace (one pipeline run), in insertion order."""
        async with get_session() as session:
            result = await session.execute(
                select(TraceEventORM)
                .where(TraceEventORM.trace_id == trace_id)
                .order_by(TraceEventORM.id)
            )
            return [_to_domain(row) for row in result.scalars().all()]

    async def list_by_claim_id(self, claim_id: str) -> List[TraceEvent]:
        """All events for a claim (across however many traces it has), in insertion order."""
        async with get_session() as session:
            result = await session.execute(
                select(TraceEventORM)
                .where(TraceEventORM.claim_id == claim_id)
                .order_by(TraceEventORM.id)
            )
            return [_to_domain(row) for row in result.scalars().all()]


def _to_domain(row: TraceEventORM) -> TraceEvent:
    """Convert a persisted row back into the vendor/storage-agnostic domain model."""
    error_json: Dict[str, Any] | None = row.error_json
    ai_metadata_json: Dict[str, Any] | None = row.ai_metadata_json
    return TraceEvent(
        event_id=row.event_id,
        trace_id=row.trace_id,
        claim_id=row.claim_id,
        component=TraceComponent(row.component),
        event_type=TraceEventType(row.event_type),
        message=row.message,
        timestamp=row.timestamp,
        duration_ms=row.duration_ms,
        confidence=row.confidence,
        metadata=row.metadata_json or {},
        error=TraceErrorInfo(**error_json) if error_json else None,
        ai_metadata=AITraceMetadata(**ai_metadata_json) if ai_metadata_json else None,
        sequence=row.id,
    )
