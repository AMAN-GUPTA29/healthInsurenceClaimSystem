"""
Policy evaluation domain models — Phase 2C.

Answers "what does the policy say about this claim?" — deterministically,
from policy_terms.json via PolicyRepository/CategoryTerms. Never decides a
payable amount (FinancialCalculationService's job) and never decides
APPROVED/PARTIAL/REJECTED/MANUAL_REVIEW (DecisionGenerationAgent's job,
Phase 2D). See ARCHITECTURE.docx "7. Policy, Financial & Fraud Logic" for
the full architectural rationale.

Rules (same as the rest of app/domain/):
- Pure Pydantic models. No database imports. No FastAPI imports. No AI SDK imports.
- No component may invent a free-form outcome vocabulary beyond PolicyRuleStatus.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.domain.models import ClaimCategory


class PolicyRuleStatus(str, Enum):
    """Controlled outcome vocabulary for one PolicyRuleFinding."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"  # ambiguous evidence — a manual-review signal, not a hard fail
    NOT_APPLICABLE = "NOT_APPLICABLE"  # the rule doesn't apply to this claim at all


class PolicyRuleFinding(BaseModel):
    """
    One structured, explainable policy check — e.g. rule="CONSULTATION_COVERED",
    status=PASSED, evidence="opd_categories.consultation.covered = true".
    `rule` is a free-form code string (same convention as
    ValidationIssue.code in app/domain/verification.py — not a closed
    enum, since the exact set of rule codes will keep growing as
    categories/conditions are added to policy_terms.json without a code
    change here). `details` carries small structured supporting values
    (dates as ISO strings, amounts as Decimal — never raw documents).
    """

    rule: str
    status: PolicyRuleStatus
    evidence: str
    details: Dict[str, Any] = Field(default_factory=dict)


class LineItemPolicyFinding(BaseModel):
    """
    Per-line-item exclusion finding — only produced for categories with
    explicit procedure/item allow/deny lists in policy_terms.json (DENTAL's
    covered_procedures/excluded_procedures, VISION's covered_items/
    excluded_items; see PolicyEngine._evaluate_line_items). Other
    categories' exclusions are claim-level (matched against extracted
    diagnosis/treatment text), not itemized — see `exclusion_applies`.
    """

    description: str
    amount: Optional[Decimal] = None
    excluded: bool
    reason: Optional[str] = None  # the matched excluded_procedures/excluded_items entry, if excluded


class PolicyEvaluationResult(BaseModel):
    """
    Output of PolicyEngine.evaluate() — one per claim. `findings` is the
    complete list of every rule checked (this doubles as the "applicable
    rules" list the assignment brief describes — a separate
    `applicable_rules` field would just be `[f.rule for f in findings]`,
    a pure duplicate, so it's not repeated as its own field).
    `passed_rules`/`failed_rules`/`warnings` are the same findings
    projected to plain rule-code lists, for a caller that just wants the
    outcome without re-deriving it from `findings`.
    """

    covered: bool
    coverage_category: ClaimCategory
    findings: List[PolicyRuleFinding] = Field(default_factory=list)
    passed_rules: List[str] = Field(default_factory=list)
    failed_rules: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    requires_pre_authorization: bool = False
    pre_authorization_provided: bool = False
    waiting_period_applies: bool = False
    exclusion_applies: bool = False
    is_network_hospital: Optional[bool] = None  # None = hospital identity unknown/ambiguous, never assumed

    sub_limit: Optional[Decimal] = None
    per_claim_limit: Optional[Decimal] = None
    annual_opd_limit: Optional[Decimal] = None
    copay_percent: float = 0.0
    network_discount_percent: float = 0.0

    line_item_findings: List[LineItemPolicyFinding] = Field(
        default_factory=list,
        description="Populated only for DENTAL/VISION claims with itemized bills — see LineItemPolicyFinding.",
    )

    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Lowered (never fabricated as 1.0) when a check had ambiguous evidence, e.g. an "
        "unnormalizable diagnosis for waiting-period/exclusion matching, or an unrecognised hospital name.",
    )
