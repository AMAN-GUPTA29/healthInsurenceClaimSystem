"""
SQLAlchemy ORM model for trace event persistence.

Kept separate from app/domain/trace.py: the domain module holds pure,
storage-agnostic Pydantic models; this module holds the SQLAlchemy mapping.
TraceRepository (trace_repository.py) is the only place that converts
between the two — no other code should import TraceEventORM directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.repositories.database import Base


class TraceEventORM(Base):
    """
    One row per trace event. `id` (autoincrement) is the authoritative
    ordering key for "many events per claim, ordering preserved" — two
    events can share a `timestamp` at pipeline speed, but never share `id`.

    Note: the column is named `metadata_json`, not `metadata` — SQLAlchemy's
    DeclarativeBase already reserves `.metadata` for the schema's MetaData
    object, so a mapped attribute of that name would collide with it.
    """

    __tablename__ = "trace_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    claim_id: Mapped[str] = mapped_column(String(64), index=True)
    component: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    error_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    ai_metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_trace_events_claim_id_id", "claim_id", "id"),
        Index("ix_trace_events_trace_id_id", "trace_id", "id"),
    )
