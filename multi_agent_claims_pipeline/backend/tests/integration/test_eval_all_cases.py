"""
Regression test: TC001-TC003 from the real test_cases.json must pass
through the real pipeline, via the real evaluation runner (not a
duplicate/simplified re-implementation of the checks).

This is the automated counterpart to scripts/run_eval.py — same code
path, so a human running the script and CI running this test can't
silently disagree.
"""

from __future__ import annotations

import pytest

from app.evaluation.runner import run_test_case


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_tc001_wrong_document_passes():
    result = await run_test_case("TC001")
    assert result.passed, result.reasons


@pytest.mark.anyio
async def test_tc002_unreadable_document_passes():
    result = await run_test_case("TC002")
    assert result.passed, result.reasons


@pytest.mark.anyio
async def test_tc003_different_patients_passes():
    result = await run_test_case("TC003")
    assert result.passed, result.reasons


@pytest.mark.anyio
async def test_tc001_trace_matches_documented_shape():
    """Section 23's documented trace shape for TC001."""
    result = await run_test_case("TC001")
    event_pairs = [(e.component.value, e.event_type.value) for e in result.trace_events]
    assert ("CLAIM_VALIDATION", "COMPLETED") in event_pairs
    assert ("DOCUMENT_VERIFICATION", "COMPLETED") in event_pairs
    assert ("CROSS_DOCUMENT_VALIDATION", "SKIPPED") in event_pairs
    assert ("DECISION_GENERATION", "STARTED") not in event_pairs  # decision never runs


@pytest.mark.anyio
async def test_tc003_trace_matches_documented_shape():
    """Section 23's documented trace shape for TC003."""
    result = await run_test_case("TC003")
    event_pairs = [(e.component.value, e.event_type.value) for e in result.trace_events]
    assert ("DOCUMENT_VERIFICATION", "COMPLETED") in event_pairs
    assert ("CROSS_DOCUMENT_VALIDATION", "COMPLETED") in event_pairs
    assert ("DECISION_GENERATION", "STARTED") not in event_pairs
