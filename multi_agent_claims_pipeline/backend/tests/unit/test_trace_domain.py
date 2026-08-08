"""
Unit tests for the trace domain models (app/domain/trace.py).

Tests that:
- TraceContext generates a trace_id and preserves claim_id
- TraceEvent enforces its typed vocabulary and optional fields
- error_info_from_exception preserves the ClaimsSystemError.recoverable semantic
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import AITimeoutError, ClaimsSystemError, DocumentUnreadableError
from app.domain.trace import (
    AITraceMetadata,
    TraceComponent,
    TraceContext,
    TraceErrorInfo,
    TraceEvent,
    TraceEventType,
    error_info_from_exception,
)


class TestTraceContext:
    def test_new_generates_trace_id(self):
        context = TraceContext.new(claim_id="CLM-ABC123")
        assert context.trace_id
        assert isinstance(context.trace_id, str)

    def test_new_preserves_claim_id(self):
        context = TraceContext.new(claim_id="CLM-ABC123")
        assert context.claim_id == "CLM-ABC123"

    def test_two_contexts_get_different_trace_ids(self):
        a = TraceContext.new(claim_id="CLM-1")
        b = TraceContext.new(claim_id="CLM-1")
        assert a.trace_id != b.trace_id


class TestTraceEvent:
    def make_event(self, **overrides) -> TraceEvent:
        defaults = dict(
            trace_id="trace-1",
            claim_id="CLM-1",
            component=TraceComponent.DOCUMENT_VERIFICATION,
            event_type=TraceEventType.COMPLETED,
        )
        defaults.update(overrides)
        return TraceEvent(**defaults)

    def test_event_id_auto_generated(self):
        event = self.make_event()
        assert event.event_id

    def test_trace_and_claim_id_preserved(self):
        event = self.make_event(trace_id="trace-42", claim_id="CLM-42")
        assert event.trace_id == "trace-42"
        assert event.claim_id == "CLM-42"

    def test_all_event_types_accepted(self):
        for event_type in (
            TraceEventType.STARTED,
            TraceEventType.COMPLETED,
            TraceEventType.FAILED,
            TraceEventType.SKIPPED,
            TraceEventType.WARNING,
        ):
            event = self.make_event(event_type=event_type)
            assert event.event_type == event_type

    def test_all_components_accepted(self):
        for component in TraceComponent:
            event = self.make_event(component=component)
            assert event.component == component

    def test_confidence_is_optional(self):
        event = self.make_event()
        assert event.confidence is None

    def test_confidence_within_bounds_accepted(self):
        event = self.make_event(confidence=0.93)
        assert event.confidence == 0.93

    def test_confidence_out_of_bounds_rejected(self):
        with pytest.raises(PydanticValidationError):
            self.make_event(confidence=1.5)

    def test_metadata_defaults_empty_dict(self):
        event = self.make_event()
        assert event.metadata == {}

    def test_metadata_preserved(self):
        event = self.make_event(metadata={"document_type": "PRESCRIPTION", "quality": "GOOD"})
        assert event.metadata == {"document_type": "PRESCRIPTION", "quality": "GOOD"}

    def test_error_is_optional(self):
        event = self.make_event()
        assert event.error is None

    def test_ai_metadata_is_optional(self):
        event = self.make_event()
        assert event.ai_metadata is None

    def test_ai_metadata_preserved(self):
        ai_meta = AITraceMetadata(provider="gemini", model="gemini-2.5-flash", latency_ms=812.5)
        event = self.make_event(ai_metadata=ai_meta)
        assert event.ai_metadata.provider == "gemini"
        assert event.ai_metadata.latency_ms == 812.5

    def test_invalid_component_string_rejected(self):
        with pytest.raises(PydanticValidationError):
            self.make_event(component="NOT_A_REAL_COMPONENT")

    def test_negative_duration_rejected(self):
        with pytest.raises(PydanticValidationError):
            self.make_event(duration_ms=-5.0)


class TestErrorInfoFromException:
    def test_claims_system_error_preserves_recoverable_true(self):
        exc = DocumentUnreadableError("F001", "too blurry")
        info = error_info_from_exception(exc)
        assert isinstance(info, TraceErrorInfo)
        assert info.recoverable is True
        assert info.code == "DOCUMENT_UNREADABLE"
        assert info.error_type == "DocumentUnreadableError"

    def test_claims_system_error_preserves_recoverable_false(self):
        from app.domain.errors import AIAuthenticationError

        exc = AIAuthenticationError("gemini")
        info = error_info_from_exception(exc)
        assert info.recoverable is False

    def test_ai_timeout_error_message_preserved(self):
        exc = AITimeoutError("gemini", 60)
        info = error_info_from_exception(exc)
        assert "gemini" in info.message
        assert "60" in info.message

    def test_generic_exception_defaults_not_recoverable(self):
        info = error_info_from_exception(ValueError("boom"))
        assert info.recoverable is False
        assert info.code is None
        assert info.error_type == "ValueError"
        assert info.message == "boom"

    def test_base_claims_system_error_has_no_special_code_loss(self):
        exc = ClaimsSystemError("generic failure", code="CUSTOM_CODE", recoverable=True)
        info = error_info_from_exception(exc)
        assert info.code == "CUSTOM_CODE"
        assert info.recoverable is True
