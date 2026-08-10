"""
Unit tests for FinancialCalculationService — Phase 2C.

Every assertion compares Decimal to Decimal — never a float literal (per
the assignment's explicit "never use float assertions" instruction).
PolicyEvaluationResult fixtures are constructed directly (not via
PolicyEngine) to isolate FinancialCalculationService's own arithmetic.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.extraction import ClaimExtractionResult, DocumentExtractionResult, HospitalBillExtraction, LineItem
from app.domain.models import Claim, ClaimCategory, ClaimSubmission, DocumentMetadata, DocumentQuality, DocumentType
from app.domain.policy_evaluation import LineItemPolicyFinding, PolicyEvaluationResult
from app.services.financial_calculation_service import FinancialCalculationService


@pytest.fixture
def service() -> FinancialCalculationService:
    return FinancialCalculationService()


def make_claim(*, amount: str, ytd: str = "0", extractions=None) -> Claim:
    submission = ClaimSubmission(
        member_id="EMP001", policy_id="PLUM_GHI_2024", claim_category=ClaimCategory.CONSULTATION,
        treatment_date=date(2024, 11, 1), claimed_amount=Decimal(amount), ytd_claims_amount=Decimal(ytd),
        documents=[DocumentMetadata(file_id="F1", file_name="bill.jpg")],
    )
    claim = Claim(submission=submission)
    if extractions is not None:
        claim.extraction_result = ClaimExtractionResult(extractions=extractions)
    return claim


def make_policy(
    *, sub_limit=None, per_claim_limit=None, annual_opd_limit=None, copay_percent=0.0,
    network_discount_percent=0.0, is_network_hospital=False, line_item_findings=None,
) -> PolicyEvaluationResult:
    return PolicyEvaluationResult(
        covered=True, coverage_category=ClaimCategory.CONSULTATION,
        sub_limit=sub_limit, per_claim_limit=per_claim_limit, annual_opd_limit=annual_opd_limit,
        copay_percent=copay_percent, network_discount_percent=network_discount_percent,
        is_network_hospital=is_network_hospital, line_item_findings=line_item_findings or [],
    )


class TestBasicCopay:
    def test_claimed_1500_copay_10_percent(self, service):
        claim = make_claim(amount="1500")
        policy = make_policy(copay_percent=10.0)
        result = service.calculate(claim, policy)
        assert result.copay_amount == Decimal("150.00")
        assert result.payable_amount == Decimal("1350.00")

    def test_zero_copay_leaves_amount_unchanged(self, service):
        claim = make_claim(amount="1500")
        policy = make_policy(copay_percent=0.0)
        result = service.calculate(claim, policy)
        assert result.copay_amount == Decimal("0.00")
        assert result.payable_amount == Decimal("1500.00")


class TestNetworkDiscountOrdering:
    def test_discount_applied_before_copay(self, service):
        """TC010-shaped: 4500, 20% network discount, 10% copay, no
        binding limit (sub_limit=None here to isolate discount/copay
        ordering — see docs/tradeoffs.md for the documented interaction
        when a category sub_limit does bind)."""
        claim = make_claim(amount="4500")
        policy = make_policy(network_discount_percent=20.0, copay_percent=10.0, is_network_hospital=True)
        result = service.calculate(claim, policy)
        assert result.network_discount_amount == Decimal("900.00")
        assert result.amount_after_network_discount == Decimal("3600.00")
        assert result.copay_amount == Decimal("360.00")
        assert result.payable_amount == Decimal("3240.00")

    def test_no_discount_when_not_network_hospital(self, service):
        claim = make_claim(amount="4500")
        policy = make_policy(network_discount_percent=20.0, copay_percent=10.0, is_network_hospital=False)
        result = service.calculate(claim, policy)
        assert result.network_discount_amount == Decimal("0")
        assert result.payable_amount == Decimal("4050.00")  # 4500 * 0.9, no discount


class TestLimits:
    def test_amount_above_sub_limit_is_capped(self, service):
        claim = make_claim(amount="2000")
        policy = make_policy(sub_limit=Decimal("1000"))
        result = service.calculate(claim, policy)
        assert result.sub_limit_applied is True
        assert result.amount_after_limits == Decimal("1000")
        assert result.payable_amount == Decimal("1000.00")

    def test_amount_within_sub_limit_is_not_capped(self, service):
        claim = make_claim(amount="800")
        policy = make_policy(sub_limit=Decimal("1000"))
        result = service.calculate(claim, policy)
        assert result.sub_limit_applied is False
        assert result.payable_amount == Decimal("800.00")

    def test_amount_above_per_claim_limit_is_capped(self, service):
        claim = make_claim(amount="7500")
        policy = make_policy(per_claim_limit=Decimal("5000"))
        result = service.calculate(claim, policy)
        assert result.per_claim_limit_applied is True
        assert result.payable_amount == Decimal("5000.00")

    def test_remaining_annual_allowance_caps_the_claim(self, service):
        # 50000 annual limit, 49000 already used -> only 1000 remains
        claim = make_claim(amount="2000", ytd="49000")
        policy = make_policy(annual_opd_limit=Decimal("50000"))
        result = service.calculate(claim, policy)
        assert result.annual_limit_applied is True
        assert result.payable_amount == Decimal("1000.00")

    def test_annual_allowance_already_exhausted_floors_at_zero(self, service):
        claim = make_claim(amount="2000", ytd="55000")
        policy = make_policy(annual_opd_limit=Decimal("50000"))
        result = service.calculate(claim, policy)
        assert result.payable_amount == Decimal("0.00")

    def test_combination_of_discount_limits_and_copay(self, service):
        # 4500 -> discount 20% -> 3600 -> sub_limit 2000 caps it -> 2000 -> copay 10% -> 1800
        claim = make_claim(amount="4500")
        policy = make_policy(
            network_discount_percent=20.0, copay_percent=10.0, is_network_hospital=True,
            sub_limit=Decimal("2000"),
        )
        result = service.calculate(claim, policy)
        assert result.amount_after_network_discount == Decimal("3600.00")
        assert result.sub_limit_applied is True
        assert result.amount_after_limits == Decimal("2000")
        assert result.payable_amount == Decimal("1800.00")


class TestZeroAndEdgeCases:
    def test_claimed_amount_equal_to_all_limits_applies_no_cap(self, service):
        claim = make_claim(amount="5000")
        policy = make_policy(sub_limit=Decimal("5000"), per_claim_limit=Decimal("5000"))
        result = service.calculate(claim, policy)
        assert result.sub_limit_applied is False
        assert result.per_claim_limit_applied is False
        assert result.payable_amount == Decimal("5000.00")


class TestRounding:
    def test_copay_rounds_half_up_to_two_decimal_places(self, service):
        """333.33 * 33% = 109.9989 -> rounds to 110.00 (ROUND_HALF_UP,
        not banker's rounding) — see docs/tradeoffs.md 'Rounding'."""
        claim = make_claim(amount="333.33")
        policy = make_policy(copay_percent=33.0)
        result = service.calculate(claim, policy)
        assert result.copay_amount == Decimal("110.00")

    def test_never_produces_a_float(self, service):
        claim = make_claim(amount="1500")
        policy = make_policy(copay_percent=10.0)
        result = service.calculate(claim, policy)
        assert isinstance(result.payable_amount, Decimal)
        assert isinstance(result.copay_amount, Decimal)
        assert isinstance(result.network_discount_amount, Decimal)


class TestDentalLineItemExclusion:
    def test_eligible_amount_excludes_flagged_line_items(self, service):
        """TC006-shaped: root canal (covered) + teeth whitening
        (policy-excluded) — eligible_amount must be computed from only
        the non-excluded line items, and payable reflects that base."""
        claim = make_claim(
            amount="12000",
            extractions=[
                DocumentExtractionResult(
                    file_id="F1", document_type=DocumentType.HOSPITAL_BILL, quality=DocumentQuality.GOOD,
                    extraction=HospitalBillExtraction(
                        line_items=[
                            LineItem(description="Root Canal Treatment", amount=Decimal("8000.00")),
                            LineItem(description="Teeth Whitening", amount=Decimal("4000.00")),
                        ],
                        total=Decimal("12000.00"), confidence=0.9,
                    ),
                )
            ],
        )
        policy = make_policy(
            sub_limit=Decimal("10000"),
            line_item_findings=[
                LineItemPolicyFinding(description="Root Canal Treatment", amount=Decimal("8000.00"), excluded=False),
                LineItemPolicyFinding(description="Teeth Whitening", amount=Decimal("4000.00"), excluded=True, reason="Teeth Whitening"),
            ],
        )
        result = service.calculate(claim, policy)
        assert result.eligible_amount == Decimal("8000")
        assert result.payable_amount == Decimal("8000.00")


class TestBillAmountReconciliation:
    def test_mismatched_total_produces_a_warning_without_altering_values(self, service):
        claim = make_claim(
            amount="1500",
            extractions=[
                DocumentExtractionResult(
                    file_id="F1", document_type=DocumentType.HOSPITAL_BILL, quality=DocumentQuality.GOOD,
                    extraction=HospitalBillExtraction(
                        line_items=[
                            LineItem(description="Consultation", amount=Decimal("1000.00")),
                            LineItem(description="Tests", amount=Decimal("300.00")),
                        ],
                        total=Decimal("1500.00"),  # sum is actually 1300, mismatch of 200
                        confidence=0.9,
                    ),
                )
            ],
        )
        policy = make_policy()
        result = service.calculate(claim, policy)
        assert result.warnings  # at least one reconciliation warning recorded
        assert any("1300" in w or "1500" in w for w in result.warnings)
        # Original extraction values must remain untouched.
        bill = claim.extraction_result.extractions[0].extraction
        assert bill.total == Decimal("1500.00")
        assert bill.line_items[0].amount == Decimal("1000.00")

    def test_consistent_bill_produces_no_reconciliation_warning(self, service):
        claim = make_claim(
            amount="1500",
            extractions=[
                DocumentExtractionResult(
                    file_id="F1", document_type=DocumentType.HOSPITAL_BILL, quality=DocumentQuality.GOOD,
                    extraction=HospitalBillExtraction(
                        line_items=[LineItem(description="Consultation", amount=Decimal("1500.00"))],
                        total=Decimal("1500.00"), confidence=0.9,
                    ),
                )
            ],
        )
        policy = make_policy()
        result = service.calculate(claim, policy)
        assert result.warnings == []
