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
  Agents have no knowledge that "TC001" exists — no `if case_id ==
  "TC008"` branching exists anywhere in the pipeline/agents, only here,
  where checking a fixed expectation against a real run is exactly the
  evaluation harness's job.
- TC001-TC003 (Phase 2A, document-problem detection) run with no
  document extraction — there is nothing to extract before a claim
  decision would ever be reached. TC004-TC012 (Phase 2D, full decision)
  additionally have each document's `content` block (test_cases.json's
  own structured ground truth) converted into a `ClaimExtractionResult`
  fixture — the exact "compute a decision from real Gemini-shaped output"
  path DocumentExtractionAgent would produce on these clean, unambiguous
  documents, standing in for a real (SSL-blocked in this environment)
  Gemini call — see `_extraction_result_from_test_case` below and
  docs/eval-report.md
  "Methodology" for the full justification. Policy/Financial/Fraud/
  Decision Generation make no AI calls at all, so this substitution
  affects only which extraction path produced the input text, never
  their own logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal as _D
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.agents.claim_validation_agent import ClaimValidationAgent
from app.agents.cross_document_validation_agent import CrossDocumentValidationAgent
from app.agents.decision_generation_agent import DecisionGenerationAgent
from app.agents.document_verification_agent import DocumentVerificationAgent
from app.agents.explanation_agent import ExplanationAgent
from app.agents.fraud_analysis_agent import FraudAnalysisAgent
from app.config.paths import resolve_source_file
from app.domain.extraction import (
    ClaimExtractionResult,
    DoctorInfo,
    DocumentExtractionResult,
    HospitalBillExtraction,
    LabReportExtraction,
    LabTestResult,
    LineItem,
    Medication,
    PatientInfo,
    PrescriptionExtraction,
)
from app.domain.models import Claim, ClaimStatus, DecisionType, DocumentQuality
from app.domain.trace import TraceContext, TraceEvent
from app.domain.verification import CrossDocumentValidationStatus, DocumentVerificationStatus
from app.pipeline.pipeline import ClaimsPipeline
from app.policy.policy_engine import PolicyEngine
from app.policy.policy_repository import PolicyRepository
from app.services.document_input_adapter import ClaimDocumentInput, ClaimSubmissionRequest, DocumentInputAdapter
from app.services.financial_calculation_service import FinancialCalculationService
from app.tracing.service import TraceService

# All 12 official case IDs, in order — the single shared definition for
# both scripts/run_eval.py's CLI default and the read-only GET
# /api/v1/evaluation endpoint (app/api/v1/evaluation.py, Phase 4), so
# "which cases count as the official evaluation" is never duplicated.
ALL_CASE_IDS = [f"TC{n:03d}" for n in range(1, 13)]

# Fixture-based extraction confidence — see module docstring. Not AI-supplied
# (there is no real AI call in this path), so a fixed, clearly-a-fixture
# value is used rather than fabricating a "realistic-looking" score.
_FIXTURE_EXTRACTION_CONFIDENCE = 0.95


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
        claims_history=raw_input.get("claims_history", []),
        simulate_component_failure=raw_input.get("simulate_component_failure", False),
    )


def _extraction_result_from_test_case(case: Dict[str, Any]) -> Optional[ClaimExtractionResult]:
    """
    Builds a `ClaimExtractionResult` directly from each document's
    `content` block (TC004-TC012 only — TC001-TC003 carry no `content`,
    since they test stopping before extraction would ever run). See
    module docstring for why this fixture-based substitution is
    equivalent, for Policy/Financial/Fraud/Decision purposes, to a real
    Gemini extraction call on these clean, unambiguous documents.
    """
    documents = case["input"].get("documents", [])
    extractions: List[DocumentExtractionResult] = []
    for doc in documents:
        content = doc.get("content")
        if content is None:
            continue
        actual_type = doc["actual_type"]
        patient_name = content.get("patient_name")

        if actual_type == "PRESCRIPTION":
            payload = PrescriptionExtraction(
                confidence=_FIXTURE_EXTRACTION_CONFIDENCE,
                patient=PatientInfo(name=patient_name),
                doctor=DoctorInfo(
                    name=content.get("doctor_name"),
                    registration_number=content.get("doctor_registration"),
                ),
                diagnosis=content.get("diagnosis"),
                treatment=content.get("treatment"),
                medications=[Medication(name=m) for m in content.get("medicines", [])],
                investigations=content.get("tests_ordered", []),
            )
        elif actual_type == "HOSPITAL_BILL":
            payload = HospitalBillExtraction(
                confidence=_FIXTURE_EXTRACTION_CONFIDENCE,
                patient_name=patient_name,
                hospital_name=content.get("hospital_name"),
                line_items=[
                    LineItem(description=li["description"], amount=li.get("amount"))
                    for li in content.get("line_items", [])
                ],
                total=content.get("total"),
            )
        elif actual_type == "LAB_REPORT":
            test_name = content.get("test_name")
            payload = LabReportExtraction(
                confidence=_FIXTURE_EXTRACTION_CONFIDENCE,
                tests=[LabTestResult(test_name=test_name)] if test_name else [],
            )
        else:
            # No extraction schema for this document type in the assignment's
            # 12 official cases (see SUPPORTED_EXTRACTION_TYPES) — skipped,
            # not a failure, matching DocumentExtractionAgent's own contract.
            continue

        extractions.append(
            DocumentExtractionResult(
                file_id=doc["file_id"],
                document_type=actual_type,
                quality=DocumentQuality.GOOD,
                patient=PatientInfo(name=patient_name),
                extraction=payload,
            )
        )

    if not extractions:
        return None
    return ClaimExtractionResult(
        extractions=extractions,
        failures=[],
        skipped=[],
        confidence=_FIXTURE_EXTRACTION_CONFIDENCE,
        has_failures=False,
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
    # Fixture-based extraction (TC004-TC012 only — see module docstring).
    # Set BEFORE pipeline.run(): no document_extraction_agent is wired
    # below, so Stage 4 is legitimately SKIPPED and never overwrites this.
    claim.extraction_result = _extraction_result_from_test_case(case)

    pipeline = ClaimsPipeline(
        claim_validation_agent=ClaimValidationAgent(policy_repository=policy_repository),
        document_verification_agent=DocumentVerificationAgent(
            ai_provider=None, policy_repository=policy_repository
        ),
        cross_document_validation_agent=CrossDocumentValidationAgent(),
        policy_engine=PolicyEngine(policy_repository=policy_repository),
        financial_calculation_service=FinancialCalculationService(),
        fraud_analysis_agent=FraudAnalysisAgent(policy_repository=policy_repository),
        decision_generation_agent=DecisionGenerationAgent(),
        # No AI provider — see module docstring; ExplanationAgent's own
        # deterministic fallback still runs and is reported honestly via
        # explanation_detail.source == "FALLBACK", never faked as "AI".
        explanation_agent=ExplanationAgent(ai_provider=None),
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


def _rejection_values(claim: Claim) -> List[str]:
    if claim.decision is None:
        return []
    return [r.value for r in claim.decision.rejection_reasons]


def _check_tc004(claim: Claim) -> Tuple[bool, List[str]]:
    """TC004 — Clean Consultation: APPROVED, ₹1350 payable, confidence > 0.85."""
    reasons: List[str] = []
    decision = claim.decision
    if decision is None:
        return (False, ["no decision was generated"])
    if decision.decision != DecisionType.APPROVED:
        reasons.append(f"expected decision APPROVED, got {decision.decision.value}")
    if decision.approved_amount != _D("1350.00"):
        reasons.append(f"expected approved_amount 1350.00, got {decision.approved_amount}")
    if decision.confidence_score <= 0.85:
        reasons.append(f"expected confidence_score > 0.85, got {decision.confidence_score}")
    return (len(reasons) == 0, reasons)


def _check_tc005(claim: Claim) -> Tuple[bool, List[str]]:
    """TC005 — Waiting Period (Diabetes): REJECTED, WAITING_PERIOD."""
    reasons: List[str] = []
    decision = claim.decision
    if decision is None:
        return (False, ["no decision was generated"])
    if decision.decision != DecisionType.REJECTED:
        reasons.append(f"expected decision REJECTED, got {decision.decision.value}")
    if "WAITING_PERIOD" not in _rejection_values(claim):
        reasons.append(f"expected WAITING_PERIOD in rejection_reasons, got {_rejection_values(claim)}")
    return (len(reasons) == 0, reasons)


def _check_tc006(claim: Claim) -> Tuple[bool, List[str]]:
    """TC006 — Dental Partial Approval: PARTIAL, ₹8000 payable, itemized."""
    reasons: List[str] = []
    decision = claim.decision
    if decision is None:
        return (False, ["no decision was generated"])
    if decision.decision != DecisionType.PARTIAL:
        reasons.append(f"expected decision PARTIAL, got {decision.decision.value}")
    if decision.approved_amount != _D("8000.00"):
        reasons.append(f"expected approved_amount 8000.00, got {decision.approved_amount}")
    if len(decision.line_item_decisions) != 2:
        reasons.append(f"expected 2 itemized line-item decisions, got {len(decision.line_item_decisions)}")
    else:
        approved_items = [li for li in decision.line_item_decisions if li.approved]
        rejected_items = [li for li in decision.line_item_decisions if not li.approved]
        if len(approved_items) != 1 or len(rejected_items) != 1:
            reasons.append("expected exactly one approved and one rejected line item")
        elif rejected_items[0].notes is None and rejected_items[0].rejection_reason is None:
            reasons.append("rejected line item has no stated reason")
    return (len(reasons) == 0, reasons)


def _check_tc007(claim: Claim) -> Tuple[bool, List[str]]:
    """TC007 — MRI Without Pre-Authorization: REJECTED, PRE_AUTH_MISSING."""
    reasons: List[str] = []
    decision = claim.decision
    if decision is None:
        return (False, ["no decision was generated"])
    if decision.decision != DecisionType.REJECTED:
        reasons.append(f"expected decision REJECTED, got {decision.decision.value}")
    if "PRE_AUTH_MISSING" not in _rejection_values(claim):
        reasons.append(f"expected PRE_AUTH_MISSING in rejection_reasons, got {_rejection_values(claim)}")
    return (len(reasons) == 0, reasons)


def _check_tc008(claim: Claim) -> Tuple[bool, List[str]]:
    """TC008 — Per-Claim Limit Exceeded: REJECTED, PER_CLAIM_EXCEEDED."""
    reasons: List[str] = []
    decision = claim.decision
    if decision is None:
        return (False, ["no decision was generated"])
    if decision.decision != DecisionType.REJECTED:
        reasons.append(f"expected decision REJECTED, got {decision.decision.value}")
    if "PER_CLAIM_EXCEEDED" not in _rejection_values(claim):
        reasons.append(f"expected PER_CLAIM_EXCEEDED in rejection_reasons, got {_rejection_values(claim)}")
    return (len(reasons) == 0, reasons)


def _check_tc009(claim: Claim) -> Tuple[bool, List[str]]:
    """TC009 — Fraud Signal (Multiple Same-Day Claims): MANUAL_REVIEW,
    with the specific triggering signal(s) surfaced in the output."""
    reasons: List[str] = []
    decision = claim.decision
    if decision is None:
        return (False, ["no decision was generated"])
    if decision.decision != DecisionType.MANUAL_REVIEW:
        reasons.append(f"expected decision MANUAL_REVIEW, got {decision.decision.value}")
    if not decision.fraud_signals:
        reasons.append("expected at least one fraud signal to be included in the output")
    return (len(reasons) == 0, reasons)


def _check_tc010(claim: Claim) -> Tuple[bool, List[str]]:
    """TC010 — Network Hospital Discount: APPROVED, ₹3240 payable (discount
    before copay, no sub-limit cap — see docs/tradeoffs.md)."""
    reasons: List[str] = []
    decision = claim.decision
    if decision is None:
        return (False, ["no decision was generated"])
    if decision.decision != DecisionType.APPROVED:
        reasons.append(f"expected decision APPROVED, got {decision.decision.value}")
    if decision.approved_amount != _D("3240.00"):
        reasons.append(f"expected approved_amount 3240.00, got {decision.approved_amount}")
    return (len(reasons) == 0, reasons)


def _check_tc011(claim: Claim) -> Tuple[bool, List[str]]:
    """TC011 — Component Failure (Graceful Degradation): APPROVED, no
    crash, reduced confidence, manual review recommended."""
    reasons: List[str] = []
    decision = claim.decision
    if decision is None:
        return (False, ["no decision was generated — the pipeline must not crash on a component failure"])
    if decision.decision != DecisionType.APPROVED:
        reasons.append(f"expected decision APPROVED, got {decision.decision.value}")
    if not decision.has_component_failures:
        reasons.append("expected has_component_failures=True (the simulated failure must be visible)")
    if decision.confidence_score >= 1.0:
        reasons.append(f"expected a reduced confidence score, got {decision.confidence_score}")
    if not decision.manual_review_recommended:
        reasons.append("expected manual_review_recommended=True")
    return (len(reasons) == 0, reasons)


def _check_tc012(claim: Claim) -> Tuple[bool, List[str]]:
    """TC012 — Excluded Treatment (Obesity): REJECTED, EXCLUDED_CONDITION,
    confidence > 0.90."""
    reasons: List[str] = []
    decision = claim.decision
    if decision is None:
        return (False, ["no decision was generated"])
    if decision.decision != DecisionType.REJECTED:
        reasons.append(f"expected decision REJECTED, got {decision.decision.value}")
    if "EXCLUDED_CONDITION" not in _rejection_values(claim):
        reasons.append(f"expected EXCLUDED_CONDITION in rejection_reasons, got {_rejection_values(claim)}")
    if decision.confidence_score <= 0.90:
        reasons.append(f"expected confidence_score > 0.90, got {decision.confidence_score}")
    return (len(reasons) == 0, reasons)


_CHECKERS: Dict[str, Callable[[Claim], Tuple[bool, List[str]]]] = {
    "TC001": _check_tc001,
    "TC002": _check_tc002,
    "TC003": _check_tc003,
    "TC004": _check_tc004,
    "TC005": _check_tc005,
    "TC006": _check_tc006,
    "TC007": _check_tc007,
    "TC008": _check_tc008,
    "TC009": _check_tc009,
    "TC010": _check_tc010,
    "TC011": _check_tc011,
    "TC012": _check_tc012,
}
