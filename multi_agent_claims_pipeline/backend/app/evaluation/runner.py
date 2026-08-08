"""
Evaluation runner — executes assignment test cases (test_cases.json)
through the actual ClaimsPipeline and checks real behavior against each
case's documented expectations.

Design:
- test_cases.json is never modified (source of truth). It's loaded,
  converted to the same ClaimSubmissionRequest shape the HTTP API accepts
  (via DocumentInputAdapter), and run through a real ClaimsPipeline —
  the exact same objects the API endpoint uses, not a parallel/fake path.
- Expected-outcome checking (what counts as PASS/FAIL for a given case)
  lives entirely in this module, never inside an agent or ClaimsPipeline.
  Agents have no knowledge that "TC001" exists.
- Only TC001-TC003 have checkers implemented (Phase 2A scope). Running
  any other case returns a result explaining no checker exists yet,
  rather than silently reporting PASS or FAIL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.agents.claim_validation_agent import ClaimValidationAgent
from app.agents.cross_document_validation_agent import CrossDocumentValidationAgent
from app.agents.document_verification_agent import DocumentVerificationAgent
from app.config.paths import resolve_source_file
from app.domain.models import Claim, ClaimStatus
from app.domain.trace import TraceContext, TraceEvent
from app.domain.verification import CrossDocumentValidationStatus, DocumentVerificationStatus
from app.pipeline.pipeline import ClaimsPipeline
from app.policy.policy_repository import PolicyRepository
from app.services.document_input_adapter import ClaimDocumentInput, ClaimSubmissionRequest, DocumentInputAdapter
from app.tracing.service import TraceService


@dataclass
class EvalResult:
    case_id: str
    case_name: str
    passed: bool
    reasons: List[str] = field(default_factory=list)
    claim: Optional[Claim] = None
    trace_events: List[TraceEvent] = field(default_factory=list)


# ── Loading ────────────────────────────────────────────────────────────────


def load_test_cases(filename: str = "test_cases.json") -> Dict[str, Any]:
    path = resolve_source_file(filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_test_case(case_id: str, filename: str = "test_cases.json") -> Dict[str, Any]:
    data = load_test_cases(filename)
    for case in data["test_cases"]:
        if case["case_id"] == case_id:
            return case
    raise KeyError(f"Test case '{case_id}' not found in {filename}")


def request_from_test_case(case: Dict[str, Any]) -> ClaimSubmissionRequest:
    """
    Map a test_cases.json case's `input` block onto ClaimSubmissionRequest.
    Field names were deliberately chosen to mirror the fixture format
    (actual_type, patient_name_on_doc, quality) so this is a near-direct
    passthrough, not a translation layer with its own logic.
    """
    raw_input = case["input"]
    documents = [
        ClaimDocumentInput(
            file_id=doc["file_id"],
            file_name=doc.get("file_name"),
            actual_type=doc.get("actual_type"),
            quality=doc.get("quality"),
            patient_name_on_doc=doc.get("patient_name_on_doc"),
        )
        for doc in raw_input.get("documents", [])
    ]
    return ClaimSubmissionRequest(
        member_id=raw_input["member_id"],
        policy_id=raw_input["policy_id"],
        claim_category=raw_input["claim_category"],
        treatment_date=raw_input["treatment_date"],
        claimed_amount=raw_input["claimed_amount"],
        hospital_name=raw_input.get("hospital_name"),
        ytd_claims_amount=raw_input.get("ytd_claims_amount", 0),
        documents=documents,
        simulate_component_failure=raw_input.get("simulate_component_failure", False),
    )


# ── Execution ─────────────────────────────────────────────────────────────


async def run_test_case(
    case_id: str,
    *,
    filename: str = "test_cases.json",
    persist: bool = False,
) -> EvalResult:
    """Run one test case through the real pipeline and check the outcome."""
    case = get_test_case(case_id, filename)
    request = request_from_test_case(case)

    policy_repository = PolicyRepository()
    adapter = DocumentInputAdapter()
    submission, classifications = adapter.to_domain(request)
    claim = Claim(submission=submission)

    pipeline = ClaimsPipeline(
        claim_validation_agent=ClaimValidationAgent(policy_repository=policy_repository),
        document_verification_agent=DocumentVerificationAgent(
            ai_provider=None, policy_repository=policy_repository
        ),
        cross_document_validation_agent=CrossDocumentValidationAgent(),
    )

    sink = None
    if persist:
        from app.repositories.trace_repository import TraceRepository

        sink = TraceRepository()
    tracer = TraceService(TraceContext.new(claim_id=claim.claim_id), sink=sink)

    claim = await pipeline.run(claim, classifications=classifications, tracer=tracer)

    checker = _CHECKERS.get(case_id)
    if checker is None:
        return EvalResult(
            case_id=case_id,
            case_name=case["case_name"],
            passed=False,
            reasons=[f"No evaluation checker implemented for {case_id} yet (out of Phase 2A scope)."],
            claim=claim,
            trace_events=tracer.events,
        )

    passed, reasons = checker(claim)
    return EvalResult(
        case_id=case_id,
        case_name=case["case_name"],
        passed=passed,
        reasons=reasons,
        claim=claim,
        trace_events=tracer.events,
    )


async def run_test_cases(case_ids: List[str], **kwargs: Any) -> List[EvalResult]:
    return [await run_test_case(case_id, **kwargs) for case_id in case_ids]


# ── Expected-Outcome Checkers (Phase 2A: TC001-TC003 only) ─────────────────


def _msg_mentions(message: str, *needles: str) -> bool:
    lowered = message.lower()
    return all(needle.lower() in lowered for needle in needles)


def _check_tc001(claim: Claim) -> Tuple[bool, List[str]]:
    """TC001 — Wrong Document: stop before a decision; name what was
    uploaded and what's missing."""
    reasons: List[str] = []
    dvr = claim.document_verification_result

    if claim.decision is not None:
        reasons.append("a decision was generated; TC001 must stop before any claim decision")
    if dvr is None or dvr.status != DocumentVerificationStatus.BLOCKED:
        reasons.append(f"expected document verification status BLOCKED, got {dvr.status if dvr else None}")
    if dvr is not None and "HOSPITAL_BILL" not in [t.value for t in dvr.missing_documents]:
        reasons.append("expected HOSPITAL_BILL to be reported as the missing document")
    if not _msg_mentions(claim.user_message or "", "prescription", "hospital bill"):
        reasons.append("user message does not name both the uploaded document type and the required type")

    return (len(reasons) == 0, reasons)


def _check_tc002(claim: Claim) -> Tuple[bool, List[str]]:
    """TC002 — Unreadable Document: identify it, ask for re-upload, don't
    reject outright."""
    reasons: List[str] = []
    dvr = claim.document_verification_result

    if claim.decision is not None:
        reasons.append("a decision was generated; TC002 must stop before any claim decision")
    if claim.status == ClaimStatus.BLOCKED:
        reasons.append("claim status is BLOCKED (hard stop) — TC002 must not be treated as a rejection")
    if dvr is None or dvr.status != DocumentVerificationStatus.NEEDS_RESUBMISSION:
        reasons.append(f"expected document verification status NEEDS_RESUBMISSION, got {dvr.status if dvr else None}")
    if not _msg_mentions(claim.user_message or "", "pharmacy bill"):
        reasons.append("user message does not specifically identify the pharmacy bill")
    if not _msg_mentions(claim.user_message or "", "re-upload"):
        reasons.append("user message does not ask the member to re-upload")

    return (len(reasons) == 0, reasons)


def _check_tc003(claim: Claim) -> Tuple[bool, List[str]]:
    """TC003 — Different Patients: detect mismatch, name both patients,
    stop before a decision."""
    reasons: List[str] = []
    cdvr = claim.cross_document_validation_result

    if claim.decision is not None:
        reasons.append("a decision was generated; TC003 must stop before any claim decision")
    if cdvr is None or cdvr.status != CrossDocumentValidationStatus.BLOCKED:
        reasons.append(f"expected cross-document validation status BLOCKED, got {cdvr.status if cdvr else None}")
    if not _msg_mentions(claim.user_message or "", "rajesh kumar", "arjun mehta"):
        reasons.append("user message does not name both patients found on the documents")

    return (len(reasons) == 0, reasons)


_CHECKERS: Dict[str, Callable[[Claim], Tuple[bool, List[str]]]] = {
    "TC001": _check_tc001,
    "TC002": _check_tc002,
    "TC003": _check_tc003,
}
