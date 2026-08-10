"""
FinancialCalculationService — Phase 2C, pipeline stage 6.

Answers "based on the applicable policy rules, what is the mathematically
eligible/payable amount?" — purely deterministic, `decimal.Decimal`
arithmetic only. No AI calls. Never decides APPROVED/PARTIAL/REJECTED/
MANUAL_REVIEW (DecisionGenerationAgent, Phase 2D) — a capped/reduced
payable_amount here is "what would be payable if approved," not itself an
approval. See docs/tradeoffs.md "Financial Calculation Order" for the
calculation-order rationale (including a known discrepancy with TC010's
own worked example) and docs/architecture.md "Financial Calculation
(Phase 2C)" for the full design rationale.

Rules:
- decimal.Decimal for every monetary value. Never float.
- The LLM never produces approved_amount/payable_amount/copay_amount/
  discount_amount as authoritative — those are computed here from
  PolicyEvaluationResult + the claim, never read from AI extraction output.
  An AI-extracted bill total is document data (evidence), not a financial
  authority.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional

from app.domain.models import Claim, FinancialBreakdown
from app.domain.policy_evaluation import PolicyEvaluationResult

_CENTS = Decimal("0.01")
_RECONCILIATION_TOLERANCE = Decimal("1.00")  # INR — small rounding/printing slack, not a real discrepancy


def _round(amount: Decimal) -> Decimal:
    return amount.quantize(_CENTS, rounding=ROUND_HALF_UP)


class FinancialCalculationService:
    """Deterministic financial calculator. See module docstring."""

    def calculate(self, claim: Claim, policy: PolicyEvaluationResult) -> FinancialBreakdown:
        submission = claim.submission
        claimed_amount = submission.claimed_amount
        steps: List[str] = [f"Claimed amount: {claimed_amount}"]
        warnings: List[str] = []

        eligible_amount = self._resolve_eligible_amount(claim, policy, claimed_amount, steps, warnings)

        # Step 1: network discount
        if policy.is_network_hospital and policy.network_discount_percent:
            discount_amount = _round(eligible_amount * Decimal(str(policy.network_discount_percent)) / Decimal(100))
            amount_after_discount = eligible_amount - discount_amount
            steps.append(
                f"Network discount ({policy.network_discount_percent}%) applied: "
                f"-{discount_amount} -> {amount_after_discount}"
            )
        else:
            discount_amount = Decimal("0")
            amount_after_discount = eligible_amount
            steps.append("No network discount applied (not a recognised network hospital, or 0%).")

        # Step 2: category sub-limit
        amount_after_sub_limit, sub_limit_applied = self._apply_cap(
            amount_after_discount, policy.sub_limit, "sub_limit", steps
        )

        # Step 3: per-claim limit
        amount_after_per_claim, per_claim_limit_applied = self._apply_cap(
            amount_after_sub_limit, policy.per_claim_limit, "per_claim_limit", steps
        )

        # Step 4: remaining annual OPD allowance
        remaining_annual: Optional[Decimal] = None
        if policy.annual_opd_limit is not None:
            remaining_annual = max(policy.annual_opd_limit - submission.ytd_claims_amount, Decimal("0"))
        amount_after_annual, annual_limit_applied = self._apply_cap(
            amount_after_per_claim, remaining_annual, "remaining annual_opd_limit", steps
        )
        amount_after_limits = amount_after_annual

        # Step 5: copay
        copay_amount = _round(amount_after_limits * Decimal(str(policy.copay_percent)) / Decimal(100))
        payable_amount = amount_after_limits - copay_amount
        if policy.copay_percent:
            steps.append(f"Co-pay ({policy.copay_percent}%) deducted: -{copay_amount} -> {payable_amount}")
        else:
            steps.append("No co-pay applicable.")

        confidence = 0.7 if warnings else 1.0

        return FinancialBreakdown(
            claimed_amount=claimed_amount,
            eligible_amount=eligible_amount,
            network_discount_percent=policy.network_discount_percent,
            network_discount_amount=discount_amount,
            amount_after_network_discount=amount_after_discount,
            sub_limit=policy.sub_limit,
            sub_limit_applied=sub_limit_applied,
            per_claim_limit=policy.per_claim_limit,
            per_claim_limit_applied=per_claim_limit_applied,
            annual_opd_limit=policy.annual_opd_limit,
            annual_limit_applied=annual_limit_applied,
            amount_after_limits=amount_after_limits,
            copay_percent=policy.copay_percent,
            copay_amount=copay_amount,
            payable_amount=payable_amount,
            currency="INR",
            calculation_steps=steps,
            warnings=warnings,
            confidence=confidence,
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _apply_cap(
        amount: Decimal, cap: Optional[Decimal], label: str, steps: List[str]
    ) -> tuple[Decimal, bool]:
        if cap is None:
            return amount, False
        if amount > cap:
            steps.append(f"Capped by {label} ({cap}): {amount} -> {cap}")
            return cap, True
        steps.append(f"Within {label} ({cap}); no cap applied.")
        return amount, False

    def _resolve_eligible_amount(
        self,
        claim: Claim,
        policy: PolicyEvaluationResult,
        claimed_amount: Decimal,
        steps: List[str],
        warnings: List[str],
    ) -> Decimal:
        """
        Base amount FinancialCalculationService starts from, before
        discount/limits/copay:
        - DENTAL/VISION with itemized findings: sum of non-excluded line
          items (policy.line_item_findings — see PolicyEngine's per-line-
          item exclusion matching). This is what makes a partial-approval
          scenario like "root canal covered, teeth whitening excluded"
          produce the correct payable base.
        - Otherwise: claimed_amount — never silently replaced by an
          AI-extracted bill total (see Bill Amount Reconciliation below),
          which is compared for a warning, not substituted.
        """
        if policy.line_item_findings:
            eligible_items = [f for f in policy.line_item_findings if not f.excluded and f.amount is not None]
            excluded_items = [f for f in policy.line_item_findings if f.excluded]
            eligible_amount = sum((f.amount for f in eligible_items), Decimal("0"))
            steps.append(
                f"Itemized bill: {len(eligible_items)} line item(s) eligible "
                f"({eligible_amount}), {len(excluded_items)} excluded by policy "
                f"({', '.join(f.description for f in excluded_items) or 'none'})."
            )
            self._check_reconciliation(claim, eligible_amount + sum(
                (f.amount for f in excluded_items if f.amount is not None), Decimal("0")
            ), steps, warnings, label="sum(line items)")
            return eligible_amount

        self._check_reconciliation(claim, claimed_amount, steps, warnings, label="claimed_amount")
        return claimed_amount

    @staticmethod
    def _check_reconciliation(
        claim: Claim, reference_amount: Decimal, steps: List[str], warnings: List[str], *, label: str
    ) -> None:
        """
        Bill amount reconciliation (see docs/tradeoffs.md "Bill Amount
        Reconciliation"): compares sum(line items) against the bill's own
        reported total/subtotal, and separately against `reference_amount`
        (claimed_amount or the itemized eligible sum). A meaningful
        mismatch is recorded as a warning — never silently "fixed", and
        the original extracted values are never altered.
        """
        if claim.extraction_result is None:
            return
        for envelope in claim.extraction_result.extractions:
            extraction = envelope.extraction
            line_items = getattr(extraction, "line_items", None)
            reported_total = getattr(extraction, "total", None)
            if line_items and reported_total is not None:
                summed = sum((item.amount for item in line_items if item.amount is not None), Decimal("0"))
                if abs(summed - reported_total) > _RECONCILIATION_TOLERANCE:
                    message = (
                        f"Bill line items sum to {summed} but the document reports a total of "
                        f"{reported_total} (difference {abs(summed - reported_total)}) — both values "
                        "preserved as extracted; not silently corrected."
                    )
                    warnings.append(message)
                    steps.append(f"Reconciliation warning: {message}")
            if reported_total is not None and abs(reported_total - reference_amount) > _RECONCILIATION_TOLERANCE:
                message = (
                    f"Document-reported total ({reported_total}) differs from {label} "
                    f"({reference_amount}) by {abs(reported_total - reference_amount)}."
                )
                warnings.append(message)
                steps.append(f"Reconciliation warning: {message}")
