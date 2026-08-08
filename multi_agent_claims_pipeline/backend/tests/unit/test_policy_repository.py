"""
Unit tests for PolicyRepository.

Exercises real behavior against the actual policy_terms.json (source of
truth) — not a mocked/fake policy file — since the whole point of this
repository is to read that file correctly.
"""

from __future__ import annotations

import pytest

from app.domain.errors import PolicyLoadError
from app.domain.models import ClaimCategory, DocumentType
from app.policy.policy_repository import PolicyRepository


@pytest.fixture
def repo() -> PolicyRepository:
    return PolicyRepository()


class TestPolicyIdentity:
    def test_policy_id_loaded(self, repo):
        assert repo.policy_id == "PLUM_GHI_2024"


class TestMembers:
    def test_known_primary_member_found(self, repo):
        member = repo.get_member("EMP001")
        assert member is not None
        assert member.name == "Rajesh Kumar"

    def test_unknown_member_returns_none(self, repo):
        assert repo.get_member("EMP999") is None

    def test_dependent_inherits_primary_join_date(self, repo):
        primary = repo.get_member("EMP001")
        dependent = repo.get_member("DEP001")
        assert dependent is not None
        assert dependent.join_date == primary.join_date

    def test_dependent_relationship_preserved(self, repo):
        dependent = repo.get_member("DEP001")
        assert dependent.relationship.value == "SPOUSE"

    def test_all_members_loaded(self, repo):
        # 10 primaries, but only DEP001/DEP002 have their own member
        # records in policy_terms.json — several employees' `dependents`
        # arrays reference DEP003-DEP006, which are NOT defined as member
        # records in the source file. That's a real gap in the source
        # data (not something to patch here — it's a protected file);
        # get_member() correctly returns None for those IDs.
        assert repo.get_member("EMP010") is not None
        assert repo.get_member("DEP002") is not None
        assert repo.get_member("DEP006") is None


class TestCategories:
    @pytest.mark.parametrize("category", list(ClaimCategory))
    def test_every_declared_category_is_recognised(self, repo, category):
        # opd_categories keys are lowercase, document_requirements keys are
        # uppercase in the source JSON — has_category must handle both.
        assert repo.has_category(category) is True


class TestDocumentRequirements:
    def test_consultation_requires_prescription_and_hospital_bill(self, repo):
        req = repo.get_document_requirements(ClaimCategory.CONSULTATION)
        assert DocumentType.PRESCRIPTION in req.required
        assert DocumentType.HOSPITAL_BILL in req.required

    def test_pharmacy_requirements(self, repo):
        req = repo.get_document_requirements(ClaimCategory.PHARMACY)
        assert DocumentType.PRESCRIPTION in req.required
        assert DocumentType.PHARMACY_BILL in req.required

    def test_optional_documents_present(self, repo):
        req = repo.get_document_requirements(ClaimCategory.CONSULTATION)
        assert DocumentType.LAB_REPORT in req.optional


class TestSubmissionRules:
    def test_minimum_claim_amount(self, repo):
        from decimal import Decimal

        assert repo.minimum_claim_amount == Decimal("500")

    def test_submission_deadline_days(self, repo):
        assert repo.submission_deadline_days == 30


class TestPolicyLoadFailure:
    def test_missing_file_raises_policy_load_error(self):
        with pytest.raises(PolicyLoadError):
            PolicyRepository("this_file_does_not_exist.json")
