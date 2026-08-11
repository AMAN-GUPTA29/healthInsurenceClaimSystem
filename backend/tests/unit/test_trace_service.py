"""
Unit tests for TraceService (app/tracing/service.py).

No database involved — uses a FakeSink to verify persistence hand-off
without needing a real repository. Tests exercise real behavior: event
sequencing, duration capture via span(), confidence/metadata passthrough,
error recording, and secret redaction.
"""

from __future__ import annotations

import asyncio

import pytest

from app.domain.errors import DocumentUnreadableError
from app.domain.trace import TraceComponent, TraceContext, TraceEvent, TraceEventType
from app.tracing.service import TraceService, redact_metadata


class FakeSink:
    """In-memory stand-in for TraceRepository — records what it's given."""

    def __init__(self) -> None:
        self.recorded: list[TraceEvent] = []

    async def record(self, event: TraceEvent) -> None:
        self.recorded.append(event)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def context() -> TraceContext:
    return TraceContext.new(claim_id="CLM-TEST01")


class TestTraceServiceBasics:
    @pytest.mark.anyio
    async def test_events_start_empty(self, context):
        tracer = TraceService(context)
        assert tracer.events == []

    @pytest.mark.anyio
    async def test_context_is_preserved(self, context):
        tracer = TraceService(context)
        assert tracer.context.claim_id == "CLM-TEST01"
        assert tracer.context.trace_id == context.trace_id

    @pytest.mark.anyio
    async def test_works_without_a_sink(self, context):
        tracer = TraceService(context, sink=None)
        event = await tracer.started(TraceComponent.CLAIM_VALIDATION)
        assert event in tracer.events


class TestTraceServiceEventTypes:
    @pytest.mark.anyio
    async def test_started_emits_started_event(self, context):
        tracer = TraceService(context)
        event = await tracer.started(TraceComponent.CLAIM_VALIDATION, "validating submission")
        assert event.event_type == TraceEventType.STARTED
        assert event.component == TraceComponent.CLAIM_VALIDATION
        assert event.message == "validating submission"
        assert event.trace_id == context.trace_id
        assert event.claim_id == context.claim_id

    @pytest.mark.anyio
    async def test_completed_emits_completed_event(self, context):
        tracer = TraceService(context)
        event = await tracer.completed(TraceComponent.DOCUMENT_VERIFICATION, message="ok")
        assert event.event_type == TraceEventType.COMPLETED

    @pytest.mark.anyio
    async def test_failed_emits_failed_event_with_error_info(self, context):
        tracer = TraceService(context)
        exc = DocumentUnreadableError("F004", "too blurry")
        event = await tracer.failed(TraceComponent.DOCUMENT_EXTRACTION, exc)
        assert event.event_type == TraceEventType.FAILED
        assert event.error is not None
        assert event.error.error_type == "DocumentUnreadableError"
        assert event.error.recoverable is True

    @pytest.mark.anyio
    async def test_failed_without_explicit_message_uses_error_message(self, context):
        tracer = TraceService(context)
        exc = DocumentUnreadableError("F004", "too blurry")
        event = await tracer.failed(TraceComponent.DOCUMENT_EXTRACTION, exc)
        assert "F004" in event.message

    @pytest.mark.anyio
    async def test_warning_emits_warning_event(self, context):
        tracer = TraceService(context)
        event = await tracer.warning(TraceComponent.POLICY_ENGINE, "low confidence extraction")
        assert event.event_type == TraceEventType.WARNING
        assert event.message == "low confidence extraction"

    @pytest.mark.anyio
    async def test_skipped_emits_skipped_event(self, context):
        tracer = TraceService(context)
        event = await tracer.skipped(TraceComponent.FRAUD_ANALYSIS, "component disabled")
        assert event.event_type == TraceEventType.SKIPPED

    @pytest.mark.anyio
    async def test_events_accumulate_in_emission_order(self, context):
        tracer = TraceService(context)
        await tracer.started(TraceComponent.CLAIM_VALIDATION)
        await tracer.completed(TraceComponent.CLAIM_VALIDATION)
        await tracer.started(TraceComponent.DOCUMENT_VERIFICATION)
        types = [e.event_type for e in tracer.events]
        assert types == [TraceEventType.STARTED, TraceEventType.COMPLETED, TraceEventType.STARTED]


class TestTraceServiceSpan:
    @pytest.mark.anyio
    async def test_span_emits_started_then_completed(self, context):
        tracer = TraceService(context)
        async with tracer.span(TraceComponent.DOCUMENT_VERIFICATION):
            await asyncio.sleep(0.01)
        assert [e.event_type for e in tracer.events] == [
            TraceEventType.STARTED,
            TraceEventType.COMPLETED,
        ]

    @pytest.mark.anyio
    async def test_span_captures_duration_on_success(self, context):
        tracer = TraceService(context)
        async with tracer.span(TraceComponent.DOCUMENT_VERIFICATION):
            await asyncio.sleep(0.01)
        completed = tracer.events[-1]
        # Not asserting exact ms — just that a real, non-negative duration was captured.
        assert completed.duration_ms is not None
        assert completed.duration_ms >= 0

    @pytest.mark.anyio
    async def test_span_emits_failed_and_reraises_on_exception(self, context):
        tracer = TraceService(context)
        with pytest.raises(ValueError):
            async with tracer.span(TraceComponent.POLICY_ENGINE):
                raise ValueError("boom")
        assert [e.event_type for e in tracer.events] == [
            TraceEventType.STARTED,
            TraceEventType.FAILED,
        ]
        assert tracer.events[-1].error.message == "boom"

    @pytest.mark.anyio
    async def test_span_failed_event_also_captures_duration(self, context):
        tracer = TraceService(context)
        with pytest.raises(ValueError):
            async with tracer.span(TraceComponent.POLICY_ENGINE):
                raise ValueError("boom")
        assert tracer.events[-1].duration_ms is not None


class TestTraceServiceConfidenceAndMetadata:
    @pytest.mark.anyio
    async def test_confidence_passed_through(self, context):
        tracer = TraceService(context)
        event = await tracer.completed(TraceComponent.DOCUMENT_EXTRACTION, confidence=0.93)
        assert event.confidence == 0.93

    @pytest.mark.anyio
    async def test_confidence_not_invented_when_absent(self, context):
        tracer = TraceService(context)
        event = await tracer.completed(TraceComponent.DOCUMENT_VERIFICATION)
        assert event.confidence is None

    @pytest.mark.anyio
    async def test_metadata_preserved(self, context):
        tracer = TraceService(context)
        event = await tracer.completed(
            TraceComponent.DOCUMENT_EXTRACTION,
            metadata={"document_type": "PRESCRIPTION", "quality": "GOOD"},
        )
        assert event.metadata == {"document_type": "PRESCRIPTION", "quality": "GOOD"}


class TestTraceServicePersistence:
    @pytest.mark.anyio
    async def test_events_persisted_to_sink(self, context):
        sink = FakeSink()
        tracer = TraceService(context, sink=sink)
        await tracer.started(TraceComponent.CLAIM_VALIDATION)
        await tracer.completed(TraceComponent.CLAIM_VALIDATION)
        assert len(sink.recorded) == 2
        assert sink.recorded == tracer.events


class TestRedactMetadata:
    def test_api_key_redacted(self):
        result = redact_metadata({"api_key": "sk-real-secret-value"})
        assert result["api_key"] == "[REDACTED]"

    def test_secret_redacted(self):
        result = redact_metadata({"client_secret": "shh"})
        assert result["client_secret"] == "[REDACTED]"

    def test_token_redacted(self):
        result = redact_metadata({"auth_token": "abc123"})
        assert result["auth_token"] == "[REDACTED]"

    def test_password_redacted(self):
        result = redact_metadata({"db_password": "hunter2"})
        assert result["db_password"] == "[REDACTED]"

    def test_authorization_header_redacted(self):
        result = redact_metadata({"Authorization": "Bearer xyz"})
        assert result["Authorization"] == "[REDACTED]"

    def test_legitimate_business_fields_untouched(self):
        result = redact_metadata(
            {
                "document_type": "PRESCRIPTION",
                "policy_rule": "WAITING_PERIOD",
                "primary_member_id": "EMP001",
                "required_days": 90,
            }
        )
        assert result == {
            "document_type": "PRESCRIPTION",
            "policy_rule": "WAITING_PERIOD",
            "primary_member_id": "EMP001",
            "required_days": 90,
        }

    @pytest.mark.anyio
    async def test_trace_service_redacts_secrets_from_metadata(self, context):
        tracer = TraceService(context)
        event = await tracer.completed(
            TraceComponent.PIPELINE,
            metadata={"api_key": "sk-should-not-leak", "component_name": "PolicyEngine"},
        )
        assert event.metadata["api_key"] == "[REDACTED]"
        assert event.metadata["component_name"] == "PolicyEngine"
        assert "sk-should-not-leak" not in str(event.metadata)
