"""
Regression test: all 12 official cases from the real test_cases.json must
pass through the real pipeline, via the real evaluation runner (not a
duplicate/simplified re-implementation of the checks).

This is the automated counterpart to scripts/run_eval.py — same code
path, so a human running the script and CI running this test can't
silently disagree. TC001-TC003 exercise early document-problem detection
(Phase 2A); TC004-TC012 exercise the complete pipeline through to a final
decision (Phase 2D), using each case's own `content` blocks as a fixture
extraction result — see app/evaluation/runner.py's module docstring.
"""

from __future__ import annotations

import pytest

from app.evaluation.runner import run_test_case


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    "case_id",
    [f"TC{n:03d}" for n in range(1, 13)],
)
@pytest.mark.anyio
async def test_official_case_passes(case_id):
    result = await run_test_case(case_id)
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


@pytest.mark.anyio
async def test_tc004_trace_reaches_decision_and_explanation():
    """TC004 is the first case that reaches a full decision. No
    document_extraction_agent is configured in the evaluation runner
    (fixture-based extraction is pre-attached to the claim instead — see
    app/evaluation/runner.py), so DOCUMENT_EXTRACTION is legitimately
    SKIPPED; every Phase 2C/2D stage after it must be COMPLETED."""
    result = await run_test_case("TC004")
    event_pairs = [(e.component.value, e.event_type.value) for e in result.trace_events]
    assert ("DOCUMENT_EXTRACTION", "SKIPPED") in event_pairs
    for component in ("POLICY_ENGINE", "FINANCIAL_CALCULATION", "FRAUD_ANALYSIS", "DECISION_GENERATION", "EXPLANATION"):
        assert (component, "COMPLETED") in event_pairs
