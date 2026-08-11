"""
Integration tests for TraceRepository persistence.

Exercises real behavior against a real (file-based) SQLite database via the
async SQLAlchemy engine — no mocking of the database layer.
"""

from __future__ import annotations

import os

import pytest

from app.domain.trace import TraceComponent, TraceContext, TraceEventType
from app.repositories.database import close_database, init_database
from app.repositories.trace_repository import TraceRepository
from app.tracing.service import TraceService

TEST_DB_PATH = "./data/test_trace_persistence.db"
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    """Initialise a fresh file-based test database for each test."""
    os.makedirs("data", exist_ok=True)
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    await init_database(TEST_DB_URL)
    yield
    await close_database()
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


class TestTraceRepositoryPersistence:
    @pytest.mark.anyio
    async def test_create_and_list_by_claim_id(self, db):
        repo = TraceRepository()
        context = TraceContext.new(claim_id="CLM-PERSIST01")
        tracer = TraceService(context, sink=repo)

        await tracer.started(TraceComponent.CLAIM_VALIDATION, "validating")
        await tracer.completed(TraceComponent.CLAIM_VALIDATION, message="validated")

        events = await repo.list_by_claim_id("CLM-PERSIST01")
        assert len(events) == 2
        assert events[0].event_type == TraceEventType.STARTED
        assert events[1].event_type == TraceEventType.COMPLETED

    @pytest.mark.anyio
    async def test_create_and_list_by_trace_id(self, db):
        repo = TraceRepository()
        context = TraceContext.new(claim_id="CLM-PERSIST02")
        tracer = TraceService(context, sink=repo)

        await tracer.started(TraceComponent.DOCUMENT_VERIFICATION)

        events = await repo.list_by_trace_id(context.trace_id)
        assert len(events) == 1
        assert events[0].trace_id == context.trace_id

    @pytest.mark.anyio
    async def test_ordering_is_preserved_across_many_events(self, db):
        repo = TraceRepository()
        context = TraceContext.new(claim_id="CLM-PERSIST03")
        tracer = TraceService(context, sink=repo)

        components = [
            TraceComponent.CLAIM_VALIDATION,
            TraceComponent.DOCUMENT_VERIFICATION,
            TraceComponent.DOCUMENT_EXTRACTION,
            TraceComponent.POLICY_ENGINE,
            TraceComponent.DECISION_GENERATION,
        ]
        for component in components:
            await tracer.started(component)

        events = await repo.list_by_claim_id("CLM-PERSIST03")
        assert [e.component for e in events] == components

    @pytest.mark.anyio
    async def test_unknown_claim_id_returns_empty_list(self, db):
        repo = TraceRepository()
        events = await repo.list_by_claim_id("CLM-DOES-NOT-EXIST")
        assert events == []

    @pytest.mark.anyio
    async def test_events_for_different_claims_do_not_mix(self, db):
        repo = TraceRepository()
        tracer_a = TraceService(TraceContext.new(claim_id="CLM-A"), sink=repo)
        tracer_b = TraceService(TraceContext.new(claim_id="CLM-B"), sink=repo)

        await tracer_a.started(TraceComponent.CLAIM_VALIDATION)
        await tracer_b.started(TraceComponent.CLAIM_VALIDATION)
        await tracer_b.completed(TraceComponent.CLAIM_VALIDATION)

        events_a = await repo.list_by_claim_id("CLM-A")
        events_b = await repo.list_by_claim_id("CLM-B")
        assert len(events_a) == 1
        assert len(events_b) == 2

    @pytest.mark.anyio
    async def test_confidence_and_metadata_round_trip(self, db):
        repo = TraceRepository()
        tracer = TraceService(TraceContext.new(claim_id="CLM-PERSIST04"), sink=repo)

        await tracer.completed(
            TraceComponent.DOCUMENT_EXTRACTION,
            confidence=0.87,
            metadata={"document_type": "PRESCRIPTION", "quality": "GOOD"},
        )

        events = await repo.list_by_claim_id("CLM-PERSIST04")
        assert events[0].confidence == 0.87
        assert events[0].metadata == {"document_type": "PRESCRIPTION", "quality": "GOOD"}

    @pytest.mark.anyio
    async def test_error_info_round_trips(self, db):
        from app.domain.errors import DocumentUnreadableError

        repo = TraceRepository()
        tracer = TraceService(TraceContext.new(claim_id="CLM-PERSIST05"), sink=repo)

        await tracer.failed(TraceComponent.DOCUMENT_EXTRACTION, DocumentUnreadableError("F004", "blurry"))

        events = await repo.list_by_claim_id("CLM-PERSIST05")
        assert events[0].error is not None
        assert events[0].error.error_type == "DocumentUnreadableError"
        assert events[0].error.recoverable is True

    @pytest.mark.anyio
    async def test_sequence_reflects_persisted_insertion_order(self, db):
        repo = TraceRepository()
        tracer = TraceService(TraceContext.new(claim_id="CLM-PERSIST06"), sink=repo)

        await tracer.started(TraceComponent.CLAIM_VALIDATION)
        await tracer.completed(TraceComponent.CLAIM_VALIDATION)

        events = await repo.list_by_claim_id("CLM-PERSIST06")
        assert events[0].sequence is not None
        assert events[1].sequence > events[0].sequence
