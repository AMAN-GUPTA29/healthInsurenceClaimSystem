"""
Unit tests for PolicyEngine — Phase 2C.

Loads the real policy_terms.json via PolicyRepository (never copies policy
values into independent test constants — per the Phase 2C brief) and
builds claims that mirror what the real pipeline hands PolicyEngine after
Stages 1-4 have run: `claim.member` populated (Phase 2A fix),
`claim.documents[i].metadata.detected_type` set (mirrors
ClaimsPipeline._apply_classifications), and `claim.extraction_result`
populated (Phase 2B).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional

import pytest

from app.domain.extraction import (
    ClaimExtractionResult,
    DentalReportExtraction,
    DocumentExtractionResult,
    DoctorInfo,
    HospitalBillExtraction,
    LineItem,
    PrescriptionExtraction,
)
from app.domain.models import (
    Claim,
    ClaimCategory,
    ClaimSubmission,
    DocumentMetadata,
    DocumentQuality,
    DocumentType,
    Member,
    RelationshipType,
)
from app.domain.policy_evaluation import PolicyRuleStatus
from app.policy.policy_engine import PolicyEngine
from app.policy.policy_repository import PolicyRepository


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def policy_repository() -> PolicyRepository:
    return PolicyRepository()


@pytest.fixture
def engine(policy_repository) -> PolicyEngine:
    return PolicyEngine(policy_repository=policy_repository)


def make_claim(
    *,
    member_id: str = "EMP001",
    member_name: str = "Rajesh Kumar",
    join_date: date = date(2024, 4, 1),
    category: ClaimCategory = ClaimCategory.CONSULTATION,
    treatment_date: date = date(2024, 11, 1),
    amount: str = "1500",
    ytd: str = "0",
    hospital_name: Optional[str] = None,
    documents: Optional[List[DocumentMetadata]] = None,
    extractions: Optional[List[DocumentExtractionResult]] = None,
    submitted_at: Optional[datetime] = None,
) -> Claim:
    documents = documents if documents is not None else [
        DocumentMetadata(
            file_id="F1", file_name="rx.jpg", detected_type=DocumentType.PRESCRIPTION, quality=DocumentQuality.GOOD
        ),
        DocumentMetadata(
            file_id="F2", file_name="bill.jpg", detected_type=DocumentType.HOSPITAL_BILL, quality=DocumentQuality.GOOD
        ),
    ]
    submission = ClaimSubmission(
        member_id=member_id, policy_id="PLUM_GHI_2024", claim_category=category,
        treatment_date=treatment_date, claimed_amount=Decimal(amount), hospital_name=hospital_name,
        ytd_claims_amount=Decimal(ytd), documents=documents,
    )
    claim = Claim(submission=submission)
    claim.member = Member(
        member_id=member_id, name=member_name, date_of_birth=date(1980, 1, 1), gender="M",
        relationship=RelationshipType.SELF, join_date=join_date,
    )
    if submitted_at is not None:
        claim.created_at = submitted_at
    else:
        # Close to treatment_date so SUBMISSION_DEADLINE passes by default —
        # individual tests override this when they specifically test the deadline.
        claim.created_at = datetime(
            treatment_date.year, treatment_date.month, treatment_date.day, tzinfo=timezone.utc
        )
    if extractions:
        claim.extraction_result = ClaimExtractionResult(extractions=extractions)
    return claim


def _finding(result, rule: str):
    return next((f for f in result.findings if f.rule == rule), None)


class TestA_CoveredConsultation:
    @pytest.mark.anyio
    async def test_consultation_is_covered(self, engine):
        claim = make_claim(category=ClaimCategory.CONSULTATION)
        result = await engine.evaluate(claim)
        assert result.covered is True
        assert _finding(result, "CONSULTATION_COVERED").status == PolicyRuleStatus.PASSED


class TestB_UncoveredCategory:
    @pytest.mark.anyio
    async def test_reports_not_covered_when_category_covered_is_false(self, engine, policy_repository, monkeypatch):
        """policy_terms.json currently marks every opd_category covered=true
        — to exercise the FAILED path without modifying the protected
        source file, monkeypatch get_category_terms for this one test."""
        from app.policy.policy_repository import CategoryTerms

        monkeypatch.setattr(
            policy_repository, "get_category_terms",
            lambda category: CategoryTerms(covered=False, sub_limit=Decimal("100")),
        )
        claim = make_claim(category=ClaimCategory.CONSULTATION)
        result = await engine.evaluate(claim)
        assert result.covered is False
        assert _finding(result, "CONSULTATION_COVERED").status == PolicyRuleStatus.FAILED


class TestC_InitialWaitingPeriod:
    @pytest.mark.anyio
    async def test_treatment_before_initial_waiting_period_fails(self, engine):
        # policy_terms.json: initial_waiting_period_days = 30
        claim = make_claim(join_date=date(2024, 10, 10), treatment_date=date(2024, 10, 20))
        result = await engine.evaluate(claim)
        assert result.waiting_period_applies is True
        assert _finding(result, "INITIAL_WAITING_PERIOD").status == PolicyRuleStatus.FAILED

    @pytest.mark.anyio
    async def test_treatment_after_initial_waiting_period_passes(self, engine):
        claim = make_claim(join_date=date(2024, 4, 1), treatment_date=date(2024, 11, 1))
        result = await engine.evaluate(claim)
        assert _finding(result, "INITIAL_WAITING_PERIOD").status == PolicyRuleStatus.PASSED


class TestD_SpecificConditionWaitingPeriod:
    @pytest.mark.anyio
    async def test_diabetes_within_waiting_period_fails(self, engine):
        # EMP005/Vikram-Joshi-shaped: joined 2024-09-01, treatment 2024-10-15 (44 days < 90)
        claim = make_claim(
            member_id="EMP005", member_name="Vikram Joshi", join_date=date(2024, 9, 1),
            treatment_date=date(2024, 10, 15),
            extractions=[_prescription_extraction("F1", diagnosis="Type 2 Diabetes Mellitus")],
        )
        result = await engine.evaluate(claim)
        assert result.waiting_period_applies is True
        assert _finding(result, "WAITING_PERIOD_DIABETES").status == PolicyRuleStatus.FAILED

    @pytest.mark.anyio
    async def test_diabetes_abbreviation_t2dm_is_recognised(self, engine):
        claim = make_claim(
            member_id="EMP005", member_name="Vikram Joshi", join_date=date(2024, 9, 1),
            treatment_date=date(2024, 10, 15),
            extractions=[_prescription_extraction("F1", diagnosis="T2DM, on treatment")],
        )
        result = await engine.evaluate(claim)
        assert _finding(result, "WAITING_PERIOD_DIABETES") is not None

    @pytest.mark.anyio
    async def test_diabetes_after_waiting_period_passes(self, engine):
        claim = make_claim(
            member_id="EMP005", member_name="Vikram Joshi", join_date=date(2024, 1, 1),
            treatment_date=date(2024, 11, 1),
            extractions=[_prescription_extraction("F1", diagnosis="Type 2 Diabetes Mellitus")],
        )
        result = await engine.evaluate(claim)
        assert _finding(result, "WAITING_PERIOD_DIABETES").status == PolicyRuleStatus.PASSED

    @pytest.mark.anyio
    async def test_no_specific_condition_matched_produces_no_specific_finding(self, engine):
        claim = make_claim(extractions=[_prescription_extraction("F1", diagnosis="Viral Fever")])
        result = await engine.evaluate(claim)
        assert not [f for f in result.findings if f.rule.startswith("WAITING_PERIOD_")]


class TestE_Exclusion:
    @pytest.mark.anyio
    async def test_bariatric_surgery_is_excluded(self, engine):
        claim = make_claim(
            member_id="EMP009", member_name="Anita Desai",
            extractions=[_prescription_extraction(
                "F1", diagnosis="Morbid Obesity - BMI 37", treatment="Bariatric Consultation and Diet Plan"
            )],
        )
        result = await engine.evaluate(claim)
        assert result.exclusion_applies is True
        assert _finding(result, "EXCLUSION_CONDITIONS").status == PolicyRuleStatus.FAILED

    @pytest.mark.anyio
    async def test_unrelated_diagnosis_is_not_excluded(self, engine):
        claim = make_claim(extractions=[_prescription_extraction("F1", diagnosis="Viral Fever")])
        result = await engine.evaluate(claim)
        assert result.exclusion_applies is False
        assert _finding(result, "EXCLUSION_CONDITIONS").status == PolicyRuleStatus.PASSED

    @pytest.mark.anyio
    async def test_no_extracted_text_is_not_applicable_not_fabricated_pass(self, engine):
        claim = make_claim(extractions=[])
        result = await engine.evaluate(claim)
        assert _finding(result, "EXCLUSION_CONDITIONS").status == PolicyRuleStatus.NOT_APPLICABLE


class TestF_SubmissionDeadline:
    @pytest.mark.anyio
    async def test_submitted_within_deadline_passes(self, engine):
        claim = make_claim(
            treatment_date=date(2024, 11, 1),
            submitted_at=datetime(2024, 11, 10, tzinfo=timezone.utc),
        )
        result = await engine.evaluate(claim)
        assert _finding(result, "SUBMISSION_DEADLINE").status == PolicyRuleStatus.PASSED

    @pytest.mark.anyio
    async def test_submitted_after_deadline_fails(self, engine):
        claim = make_claim(
            treatment_date=date(2024, 11, 1),
            submitted_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
        )
        result = await engine.evaluate(claim)
        assert _finding(result, "SUBMISSION_DEADLINE").status == PolicyRuleStatus.FAILED


class TestG_MinimumClaimAmount:
    @pytest.mark.anyio
    async def test_at_minimum_passes(self, engine, policy_repository):
        claim = make_claim(amount=str(policy_repository.minimum_claim_amount))
        result = await engine.evaluate(claim)
        assert _finding(result, "MINIMUM_CLAIM_AMOUNT").status == PolicyRuleStatus.PASSED

    @pytest.mark.anyio
    async def test_below_minimum_fails(self, engine, policy_repository):
        below = policy_repository.minimum_claim_amount - Decimal("1")
        claim = make_claim(amount=str(below))
        result = await engine.evaluate(claim)
        assert _finding(result, "MINIMUM_CLAIM_AMOUNT").status == PolicyRuleStatus.FAILED


class TestH_PerClaimLimit:
    @pytest.mark.anyio
    async def test_within_per_claim_limit_passes(self, engine, policy_repository):
        claim = make_claim(amount=str(policy_repository.per_claim_limit))
        result = await engine.evaluate(claim)
        assert _finding(result, "PER_CLAIM_LIMIT").status == PolicyRuleStatus.PASSED

    @pytest.mark.anyio
    async def test_above_per_claim_limit_fails(self, engine, policy_repository):
        above = policy_repository.per_claim_limit + Decimal("1")
        claim = make_claim(amount=str(above))
        result = await engine.evaluate(claim)
        assert _finding(result, "PER_CLAIM_LIMIT").status == PolicyRuleStatus.FAILED


class TestI_CategorySubLimit:
    @pytest.mark.anyio
    async def test_within_sub_limit_passes(self, engine, policy_repository):
        terms = policy_repository.get_category_terms(ClaimCategory.CONSULTATION)
        claim = make_claim(amount=str(terms.sub_limit))
        result = await engine.evaluate(claim)
        assert _finding(result, "SUB_LIMIT").status == PolicyRuleStatus.PASSED

    @pytest.mark.anyio
    async def test_above_sub_limit_fails(self, engine, policy_repository):
        terms = policy_repository.get_category_terms(ClaimCategory.CONSULTATION)
        above = terms.sub_limit + Decimal("1")
        claim = make_claim(amount=str(above))
        result = await engine.evaluate(claim)
        assert _finding(result, "SUB_LIMIT").status == PolicyRuleStatus.FAILED


class TestJ_Copay:
    @pytest.mark.anyio
    async def test_copay_percent_matches_policy(self, engine, policy_repository):
        terms = policy_repository.get_category_terms(ClaimCategory.CONSULTATION)
        claim = make_claim()
        result = await engine.evaluate(claim)
        assert result.copay_percent == terms.copay_percent
        assert _finding(result, "COPAY").status == PolicyRuleStatus.PASSED


class TestK_NetworkHospital:
    @pytest.mark.anyio
    async def test_known_network_hospital_passes(self, engine, policy_repository):
        hospital = policy_repository.network_hospitals[0]
        claim = make_claim(hospital_name=hospital)
        result = await engine.evaluate(claim)
        assert result.is_network_hospital is True
        assert _finding(result, "NETWORK_HOSPITAL").status == PolicyRuleStatus.PASSED


class TestL_NonNetworkHospital:
    @pytest.mark.anyio
    async def test_unknown_hospital_is_not_applicable_not_failed(self, engine):
        claim = make_claim(hospital_name="Random Local Clinic")
        result = await engine.evaluate(claim)
        assert result.is_network_hospital is False
        assert _finding(result, "NETWORK_HOSPITAL").status == PolicyRuleStatus.NOT_APPLICABLE

    @pytest.mark.anyio
    async def test_no_hospital_name_is_a_warning_not_assumed_false(self, engine):
        claim = make_claim(hospital_name=None)
        result = await engine.evaluate(claim)
        assert result.is_network_hospital is None
        assert _finding(result, "NETWORK_HOSPITAL").status == PolicyRuleStatus.WARNING


class TestM_PreAuthorizationRequirement:
    @pytest.mark.anyio
    async def test_high_value_diagnostic_without_letter_fails(self, engine):
        claim = make_claim(
            member_id="EMP007", member_name="Suresh Patil", category=ClaimCategory.DIAGNOSTIC,
            amount="15000",
            documents=[
                DocumentMetadata(file_id="F1", file_name="rx.jpg", detected_type=DocumentType.PRESCRIPTION),
                DocumentMetadata(file_id="F2", file_name="lab.jpg", detected_type=DocumentType.LAB_REPORT),
                DocumentMetadata(file_id="F3", file_name="bill.jpg", detected_type=DocumentType.HOSPITAL_BILL),
            ],
            extractions=[_prescription_extraction("F1", diagnosis="Suspected Lumbar Disc Herniation", treatment="MRI Lumbar Spine ordered")],
        )
        result = await engine.evaluate(claim)
        assert result.requires_pre_authorization is True
        assert result.pre_authorization_provided is False
        assert _finding(result, "PRE_AUTHORIZATION").status == PolicyRuleStatus.FAILED

    @pytest.mark.anyio
    async def test_pre_auth_letter_present_passes(self, engine):
        claim = make_claim(
            member_id="EMP007", member_name="Suresh Patil", category=ClaimCategory.DIAGNOSTIC,
            amount="15000",
            documents=[
                DocumentMetadata(file_id="F1", file_name="rx.jpg", detected_type=DocumentType.PRESCRIPTION),
                DocumentMetadata(file_id="F2", file_name="preauth.jpg", detected_type=DocumentType.PRE_AUTH_LETTER),
            ],
            extractions=[_prescription_extraction("F1", diagnosis="Suspected Lumbar Disc Herniation", treatment="MRI Lumbar Spine ordered")],
        )
        result = await engine.evaluate(claim)
        assert result.requires_pre_authorization is True
        assert result.pre_authorization_provided is True
        assert _finding(result, "PRE_AUTHORIZATION").status == PolicyRuleStatus.PASSED

    @pytest.mark.anyio
    async def test_low_value_diagnostic_does_not_require_pre_auth(self, engine):
        claim = make_claim(category=ClaimCategory.DIAGNOSTIC, amount="2000", extractions=[])
        result = await engine.evaluate(claim)
        assert result.requires_pre_authorization is False
        assert _finding(result, "PRE_AUTHORIZATION").status == PolicyRuleStatus.NOT_APPLICABLE


class TestN_DentalCoveredProcedure:
    @pytest.mark.anyio
    async def test_root_canal_is_not_excluded(self, engine):
        claim = make_claim(
            member_id="EMP002", member_name="Priya Singh", category=ClaimCategory.DENTAL, amount="8000",
            documents=[DocumentMetadata(file_id="F1", file_name="bill.jpg", detected_type=DocumentType.HOSPITAL_BILL)],
            extractions=[_hospital_bill_extraction("F1", line_items=[
                LineItem(description="Root Canal Treatment", amount=Decimal("8000.00")),
            ])],
        )
        result = await engine.evaluate(claim)
        assert result.line_item_findings[0].excluded is False


class TestO_DentalExcludedProcedure:
    @pytest.mark.anyio
    async def test_teeth_whitening_is_excluded(self, engine):
        claim = make_claim(
            member_id="EMP002", member_name="Priya Singh", category=ClaimCategory.DENTAL, amount="12000",
            documents=[DocumentMetadata(file_id="F1", file_name="bill.jpg", detected_type=DocumentType.HOSPITAL_BILL)],
            extractions=[_hospital_bill_extraction("F1", line_items=[
                LineItem(description="Root Canal Treatment", amount=Decimal("8000.00")),
                LineItem(description="Teeth Whitening", amount=Decimal("4000.00")),
            ])],
        )
        result = await engine.evaluate(claim)
        assert result.exclusion_applies is True
        excluded = {f.description: f.excluded for f in result.line_item_findings}
        assert excluded["Teeth Whitening"] is True
        assert excluded["Root Canal Treatment"] is False


class TestP_VisionExcludedItem:
    @pytest.mark.anyio
    async def test_lasik_surgery_is_excluded(self, engine):
        claim = make_claim(
            category=ClaimCategory.VISION, amount="15000",
            documents=[
                DocumentMetadata(file_id="F1", file_name="rx.jpg", detected_type=DocumentType.PRESCRIPTION),
                DocumentMetadata(file_id="F2", file_name="bill.jpg", detected_type=DocumentType.HOSPITAL_BILL),
            ],
            extractions=[
                _prescription_extraction("F1"),
                _hospital_bill_extraction("F2", line_items=[
                    LineItem(description="LASIK Surgery", amount=Decimal("15000.00")),
                ]),
            ],
        )
        result = await engine.evaluate(claim)
        assert result.line_item_findings[0].excluded is True


class TestQ_AlternativeMedicinePractitionerAndSession:
    @pytest.mark.anyio
    async def test_registered_practitioner_number_present_passes(self, engine):
        claim = make_claim(
            member_id="EMP006", member_name="Kavita Nair", category=ClaimCategory.ALTERNATIVE_MEDICINE,
            amount="4000",
            extractions=[_prescription_extraction(
                "F1", diagnosis="Chronic Joint Pain",
                doctor=DoctorInfo(name="Vaidya T. Krishnan", registration_number="AYUR/KL/2345/2019"),
            )],
        )
        result = await engine.evaluate(claim)
        assert _finding(result, "REGISTERED_PRACTITIONER").status == PolicyRuleStatus.PASSED

    @pytest.mark.anyio
    async def test_missing_registration_number_is_a_warning(self, engine):
        claim = make_claim(
            member_id="EMP006", member_name="Kavita Nair", category=ClaimCategory.ALTERNATIVE_MEDICINE,
            amount="4000",
            extractions=[_prescription_extraction("F1", diagnosis="Chronic Joint Pain")],
        )
        result = await engine.evaluate(claim)
        assert _finding(result, "REGISTERED_PRACTITIONER").status == PolicyRuleStatus.WARNING

    @pytest.mark.anyio
    async def test_session_limit_is_reported_as_warning_not_fabricated(self, engine, policy_repository):
        terms = policy_repository.get_category_terms(ClaimCategory.ALTERNATIVE_MEDICINE)
        claim = make_claim(
            member_id="EMP006", member_name="Kavita Nair", category=ClaimCategory.ALTERNATIVE_MEDICINE, amount="4000",
        )
        result = await engine.evaluate(claim)
        finding = _finding(result, "SESSION_LIMIT")
        assert finding.status == PolicyRuleStatus.WARNING
        assert str(terms.max_sessions_per_year) in finding.evidence


class TestR_PharmacyPrescriptionRequirement:
    @pytest.mark.anyio
    async def test_prescription_present_passes(self, engine):
        claim = make_claim(
            member_id="EMP004", member_name="Sneha Reddy", category=ClaimCategory.PHARMACY, amount="800",
            documents=[
                DocumentMetadata(file_id="F1", file_name="rx.jpg", detected_type=DocumentType.PRESCRIPTION),
                DocumentMetadata(file_id="F2", file_name="bill.jpg", detected_type=DocumentType.PHARMACY_BILL),
            ],
        )
        result = await engine.evaluate(claim)
        assert _finding(result, "PRESCRIPTION_REQUIRED").status == PolicyRuleStatus.PASSED

    @pytest.mark.anyio
    async def test_missing_prescription_fails(self, engine):
        claim = make_claim(
            member_id="EMP004", member_name="Sneha Reddy", category=ClaimCategory.PHARMACY, amount="800",
            documents=[DocumentMetadata(file_id="F2", file_name="bill.jpg", detected_type=DocumentType.PHARMACY_BILL)],
        )
        result = await engine.evaluate(claim)
        assert _finding(result, "PRESCRIPTION_REQUIRED").status == PolicyRuleStatus.FAILED


class TestS_DiagnosticHighValuePreAuth:
    @pytest.mark.anyio
    async def test_high_value_test_name_in_text_triggers_pre_auth(self, engine):
        claim = make_claim(
            member_id="EMP007", member_name="Suresh Patil", category=ClaimCategory.DIAGNOSTIC, amount="8000",
            extractions=[_prescription_extraction("F1", treatment="CT Scan of the abdomen ordered")],
        )
        result = await engine.evaluate(claim)
        assert result.requires_pre_authorization is True

    @pytest.mark.anyio
    async def test_amount_above_threshold_triggers_pre_auth_even_without_named_test(self, engine):
        claim = make_claim(
            member_id="EMP007", member_name="Suresh Patil", category=ClaimCategory.DIAGNOSTIC, amount="10001",
            extractions=[],
        )
        result = await engine.evaluate(claim)
        assert result.requires_pre_authorization is True


class TestT_NetworkHospitalConfidenceRelevance:
    """
    Phase 3 correctness fix: an unresolvable hospital name (NETWORK_HOSPITAL
    WARNING) must only degrade `confidence` when network status could
    plausibly change the payable amount — never when the claim is already
    headed for a claim-level rejection unrelated to money (TC012, an
    obesity-exclusion rejection, expects confidence_score above 0.90; the
    hospital is irrelevant to that outcome), and never for a category with
    no network discount at all (network status can't affect a payable
    amount that doesn't exist).
    """

    @pytest.mark.anyio
    async def test_unknown_hospital_caps_confidence_when_otherwise_clean(self, engine):
        """CONSULTATION (20% network discount) with no hospital name and no
        other rejection reason — network status is genuinely undetermined
        AND could have mattered, so the 0.6 cap still applies."""
        claim = make_claim(hospital_name=None, extractions=[_prescription_extraction("F1", diagnosis="Viral Fever")])
        result = await engine.evaluate(claim)
        assert _finding(result, "NETWORK_HOSPITAL").status == PolicyRuleStatus.WARNING
        assert result.confidence == 0.6

    @pytest.mark.anyio
    async def test_unknown_hospital_does_not_cap_confidence_when_claim_already_rejected(self, engine):
        """TC012-shaped: obesity exclusion (a claim-level rejection) means
        the claim never reaches a network-discount calculation at all — an
        unknown hospital name must not drag confidence down for a reason
        that can't affect the outcome."""
        claim = make_claim(
            member_id="EMP009", member_name="Anita Desai", hospital_name=None,
            extractions=[_prescription_extraction("F1", diagnosis="Morbid Obesity", treatment="Bariatric Consultation")],
        )
        result = await engine.evaluate(claim)
        assert _finding(result, "NETWORK_HOSPITAL").status == PolicyRuleStatus.WARNING
        assert result.exclusion_applies is True
        assert result.confidence > 0.6

    @pytest.mark.anyio
    async def test_unknown_hospital_does_not_cap_confidence_for_category_with_no_network_discount(self, engine):
        """DENTAL has no network_discount_percent configured — an unknown
        hospital name cannot possibly change a dental claim's payable
        amount, so it must not reduce confidence either."""
        claim = make_claim(
            category=ClaimCategory.DENTAL, hospital_name=None,
            documents=[DocumentMetadata(file_id="F1", file_name="bill.jpg", detected_type=DocumentType.HOSPITAL_BILL, quality=DocumentQuality.GOOD)],
            extractions=[_hospital_bill_extraction("F1", line_items=[LineItem(description="Root Canal Treatment", amount=Decimal("5000"))], total=Decimal("5000"))],
        )
        result = await engine.evaluate(claim)
        assert _finding(result, "NETWORK_HOSPITAL").status == PolicyRuleStatus.WARNING
        assert result.confidence > 0.6


# ── Shared fixture builders ──────────────────────────────────────────────────


def _prescription_extraction(
    file_id: str, *, diagnosis: Optional[str] = None, treatment: Optional[str] = None,
    doctor: Optional[DoctorInfo] = None,
) -> DocumentExtractionResult:
    return DocumentExtractionResult(
        file_id=file_id, document_type=DocumentType.PRESCRIPTION, quality=DocumentQuality.GOOD,
        extraction=PrescriptionExtraction(
            diagnosis=diagnosis, treatment=treatment, doctor=doctor or DoctorInfo(), confidence=0.9,
        ),
    )


def _hospital_bill_extraction(
    file_id: str, *, line_items: Optional[List[LineItem]] = None, total: Optional[Decimal] = None,
    hospital_name: Optional[str] = None,
) -> DocumentExtractionResult:
    return DocumentExtractionResult(
        file_id=file_id, document_type=DocumentType.HOSPITAL_BILL, quality=DocumentQuality.GOOD,
        extraction=HospitalBillExtraction(
            hospital_name=hospital_name, line_items=line_items or [], total=total, confidence=0.9,
        ),
    )
