"""
Official evaluation report endpoint — Phase 4.

GET /api/v1/evaluation — runs all 12 official test_cases.json cases
through the real, complete ClaimsPipeline (exactly app/evaluation/runner.py,
the same code path as `scripts/run_eval.py` and
tests/integration/test_eval_all_cases.py) and returns a structured
PASS/FAIL report. Read-only, no persistence, no side effects.

The backend is the single source of truth for evaluation results — every
number here (expected decision/amount, straight from test_cases.json;
actual decision/amount, straight from a real pipeline run) is computed
here and only here. The frontend Reports page renders this response
directly; it never recomputes or hardcodes a result.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.domain.models import ClaimStatus, DecisionType
from app.evaluation.runner import ALL_CASE_IDS, get_test_case, run_test_cases

router = APIRouter()


class EvalCaseResult(BaseModel):
    case_id: str
    case_name: str
    passed: bool
    reasons: List[str]
    # Straight from test_cases.json's own `expected` block — never invented.
    expected_decision: Optional[DecisionType] = None
    expected_approved_amount: Optional[Decimal] = None
    # From the real pipeline run this request just performed.
    actual_status: Optional[ClaimStatus] = None
    actual_decision: Optional[DecisionType] = None
    actual_approved_amount: Optional[Decimal] = None
    actual_confidence: Optional[float] = None


class EvaluationReportResponse(BaseModel):
    total: int
    passed: int
    all_passed: bool
    results: List[EvalCaseResult]


@router.get(
    "/evaluation",
    response_model=EvaluationReportResponse,
    summary="Official Evaluation Report",
    description=(
        "Runs all 12 official test_cases.json cases through the real "
        "pipeline and returns PASS/FAIL with expected vs. actual "
        "decision/amount for each — the same result "
        "`python scripts/run_eval.py` prints."
    ),
    tags=["Evaluation"],
)
async def get_evaluation_report() -> EvaluationReportResponse:
    results = await run_test_cases(ALL_CASE_IDS)

    case_results: List[EvalCaseResult] = []
    for result in results:
        expected = get_test_case(result.case_id).get("expected", {})
        expected_amount = expected.get("approved_amount")

        decision = result.claim.decision if result.claim else None
        case_results.append(
            EvalCaseResult(
                case_id=result.case_id,
                case_name=result.case_name,
                passed=result.passed,
                reasons=result.reasons,
                expected_decision=expected.get("decision"),
                expected_approved_amount=(
                    Decimal(str(expected_amount)) if expected_amount is not None else None
                ),
                actual_status=result.claim.status if result.claim else None,
                actual_decision=decision.decision if decision else None,
                actual_approved_amount=decision.approved_amount if decision else None,
                actual_confidence=decision.confidence_score if decision else None,
            )
        )

    passed_count = sum(1 for r in case_results if r.passed)
    return EvaluationReportResponse(
        total=len(case_results),
        passed=passed_count,
        all_passed=passed_count == len(case_results),
        results=case_results,
    )
