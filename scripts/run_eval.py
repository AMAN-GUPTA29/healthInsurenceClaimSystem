#!/usr/bin/env python
"""
Run all 12 official assignment test cases through the real claims
pipeline and print a PASS/FAIL report with the full trace for each case.

Usage (from the backend/ virtualenv):
    python ../scripts/run_eval.py
    python ../scripts/run_eval.py TC001 TC008

TC001-TC003 exercise early document-problem detection (Phase 2A).
TC004-TC012 run the complete pipeline through to a final decision
(Phase 2D), using each test case's own `content` blocks as a fixture
extraction result in place of a real (SSL-blocked in this environment)
Gemini call — see app/evaluation/runner.py's module docstring and
docs/eval-report.md "Methodology" for why this is equivalent for
evaluation purposes.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make `app` importable when run directly (this script lives in
# multi_agent_claims_pipeline/scripts/, the package root is ../backend).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.evaluation.runner import ALL_CASE_IDS, EvalResult, run_test_cases  # noqa: E402

DEFAULT_CASE_IDS = ALL_CASE_IDS


def _print_result(result: EvalResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(f"\n{'=' * 70}")
    print(f"{result.case_id} — {result.case_name}: {status}")
    print("=" * 70)

    if result.claim is not None:
        print(f"  status:        {result.claim.status.value}")
        print(f"  stopped_at:    {result.claim.stopped_at}")
        print(f"  user_message:  {result.claim.user_message}")

    if result.reasons:
        print("  reasons:")
        for reason in result.reasons:
            print(f"    - {reason}")

    print("  trace:")
    for event in result.trace_events:
        conf = f" confidence={event.confidence:.2f}" if event.confidence is not None else ""
        print(f"    {event.component.value:<28} {event.event_type.value:<10}{conf}  {event.message}")


async def main(case_ids: list[str]) -> int:
    results = await run_test_cases(case_ids)
    for result in results:
        _print_result(result)

    print(f"\n{'=' * 70}")
    passed = sum(1 for r in results if r.passed)
    print(f"SUMMARY: {passed}/{len(results)} passed")
    print("=" * 70)

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    case_ids = sys.argv[1:] or DEFAULT_CASE_IDS
    exit_code = asyncio.run(main(case_ids))
    sys.exit(exit_code)
