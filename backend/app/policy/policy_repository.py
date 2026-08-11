"""
PolicyRepository — read-only access to policy_terms.json.

This is a data-access layer, not a decision engine: it answers "what does
the policy say" (member roster, document requirements, minimum claim
amount) without evaluating coverage, waiting periods, exclusions, co-pay,
or any other decision logic. Those remain the job of the (still-stub)
PolicyEngine in app/policy/policy_engine.py, planned for a later phase.

Rule: policy_terms.json is the single source of truth. Nothing here
hardcodes a document requirement, a category, or a minimum amount as a
Python literal — every value is read from the loaded JSON.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.config.paths import resolve_source_file
from app.domain.errors import PolicyLoadError
from app.domain.models import ClaimCategory, DocumentType, Member, RelationshipType


class DocumentRequirement(BaseModel):
    """Required vs. optional document types for one claim category."""

    required: List[DocumentType]
    optional: List[DocumentType]


class CategoryTerms(BaseModel):
    """
    The full opd_categories.<category> block for one claim category,
    verbatim from policy_terms.json. A single model (rather than one class
    per category) because it's a direct typed mirror of one JSON object —
    each category simply leaves its irrelevant fields at their defaults
    (None / empty list), matching the source data's own shape instead of
    inventing six near-duplicate classes. Consumed by PolicyEngine
    (app/policy/policy_engine.py), never by anything outside app/policy/.
    """

    covered: bool = False
    sub_limit: Optional[Decimal] = None
    copay_percent: float = 0.0
    network_discount_percent: float = 0.0
    requires_prescription: bool = False
    requires_pre_auth: bool = False
    # Diagnostic-specific
    pre_auth_threshold: Optional[Decimal] = None
    high_value_tests_requiring_pre_auth: List[str] = []
    # Pharmacy-specific
    branded_drug_copay_percent: Optional[float] = None
    generic_mandatory: bool = False
    # Dental-specific
    requires_dental_report: bool = False
    covered_procedures: List[str] = []
    excluded_procedures: List[str] = []
    # Vision-specific
    covered_items: List[str] = []
    excluded_items: List[str] = []
    # Alternative-medicine-specific
    requires_registered_practitioner: bool = False
    max_sessions_per_year: Optional[int] = None
    covered_systems: List[str] = []


class WaitingPeriods(BaseModel):
    """Verbatim mirror of policy_terms.json's waiting_periods block."""

    initial_waiting_period_days: int
    pre_existing_conditions_days: int
    specific_conditions: Dict[str, int]  # e.g. {"diabetes": 90, ...} — lowercase keys, as in the source


class Exclusions(BaseModel):
    """Verbatim mirror of policy_terms.json's exclusions block."""

    conditions: List[str]
    dental_exclusions: List[str]
    vision_exclusions: List[str]


class PreAuthorizationRules(BaseModel):
    """Verbatim mirror of policy_terms.json's pre_authorization block.
    `required_for` is free-text (human-readable descriptions, not
    structured category/threshold pairs) — PolicyEngine's actual
    pre-authorization *determination* uses the structured
    `CategoryTerms.requires_pre_auth`/`pre_auth_threshold`/
    `high_value_tests_requiring_pre_auth` fields instead; this block is
    evidence/reference text only."""

    required_for: List[str]
    validity_days: int


class FraudThresholds(BaseModel):
    """Verbatim mirror of policy_terms.json's fraud_thresholds block."""

    same_day_claims_limit: int
    monthly_claims_limit: int
    high_value_claim_threshold: Decimal
    auto_manual_review_above: Decimal
    fraud_score_manual_review_threshold: float


class PolicyRepository:
    """
    Loads and caches policy_terms.json, exposing typed read access to the
    fields ClaimValidationAgent and DocumentVerificationAgent need.
    """

    def __init__(self, policy_file_path: str = "policy_terms.json") -> None:
        try:
            resolved_path = resolve_source_file(policy_file_path)
            with open(resolved_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except FileNotFoundError as exc:
            raise PolicyLoadError(policy_file_path, str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise PolicyLoadError(policy_file_path, f"invalid JSON: {exc}") from exc

        self._members_by_id: Dict[str, Member] = self._load_members()

    # ── Members ───────────────────────────────────────────────────────────────

    def _load_members(self) -> Dict[str, Member]:
        """
        Two passes: dependents in policy_terms.json (e.g. DEP001) carry no
        `join_date` of their own — they inherit coverage start from their
        primary member. Pass 1 loads every primary (which does have
        `join_date`); pass 2 loads dependents, resolving their join_date
        from the already-loaded primary.
        """
        raw_members: List[dict] = self._data.get("members", [])
        members: Dict[str, Member] = {}

        primaries = [r for r in raw_members if "join_date" in r]
        dependents = [r for r in raw_members if "join_date" not in r]

        for raw in primaries:
            members[raw["member_id"]] = Member(
                member_id=raw["member_id"],
                name=raw["name"],
                date_of_birth=raw["date_of_birth"],
                gender=raw["gender"],
                relationship=RelationshipType(raw["relationship"]),
                join_date=raw["join_date"],
                primary_member_id=raw.get("primary_member_id"),
                dependents=raw.get("dependents", []),
            )

        for raw in dependents:
            primary = members.get(raw.get("primary_member_id", ""))
            join_date = primary.join_date if primary is not None else raw.get("join_date")
            members[raw["member_id"]] = Member(
                member_id=raw["member_id"],
                name=raw["name"],
                date_of_birth=raw["date_of_birth"],
                gender=raw["gender"],
                relationship=RelationshipType(raw["relationship"]),
                join_date=join_date,
                primary_member_id=raw.get("primary_member_id"),
                dependents=raw.get("dependents", []),
            )

        return members

    def get_member(self, member_id: str) -> Optional[Member]:
        """Look up a member by ID. Returns None if not found — no exception."""
        return self._members_by_id.get(member_id)

    # ── Policy Identity ───────────────────────────────────────────────────────

    @property
    def policy_id(self) -> str:
        return self._data["policy_id"]

    # ── Categories ────────────────────────────────────────────────────────────

    def has_category(self, category: ClaimCategory) -> bool:
        """
        Structural check only: is this category a key in policy_terms.json's
        opd_categories? (Whether it's actually *covered* is a coverage
        decision — out of scope here.)

        Note: opd_categories keys are lowercase ("consultation") while
        document_requirements keys are uppercase ("CONSULTATION") in the
        source JSON — an inconsistency in the file itself, not something we
        can normalise there (it's a protected source-of-truth file). Match
        case-insensitively here instead.
        """
        opd_categories = self._data.get("opd_categories", {})
        return category.value.lower() in {k.lower() for k in opd_categories}

    # ── Document Requirements ────────────────────────────────────────────────

    def get_document_requirements(self, category: ClaimCategory) -> DocumentRequirement:
        """
        Required/optional document types for a claim category, read
        directly from policy_terms.json's document_requirements block.
        """
        raw = self._data.get("document_requirements", {}).get(category.value)
        if raw is None:
            return DocumentRequirement(required=[], optional=[])
        return DocumentRequirement(
            required=[DocumentType(t) for t in raw.get("required", [])],
            optional=[DocumentType(t) for t in raw.get("optional", [])],
        )

    # ── Submission Rules ──────────────────────────────────────────────────────

    @property
    def minimum_claim_amount(self):
        from decimal import Decimal

        return Decimal(str(self._data["submission_rules"]["minimum_claim_amount"]))

    @property
    def submission_deadline_days(self) -> int:
        return int(self._data["submission_rules"]["deadline_days_from_treatment"])

    # ── Policy Validity (Phase 2C) ───────────────────────────────────────────

    @property
    def policy_start_date(self) -> date:
        return date.fromisoformat(self._data["policy_holder"]["policy_start_date"])

    @property
    def policy_end_date(self) -> date:
        return date.fromisoformat(self._data["policy_holder"]["policy_end_date"])

    @property
    def renewal_status(self) -> str:
        return self._data["policy_holder"]["renewal_status"]

    def is_policy_active(self, as_of: date) -> bool:
        """Renewal status is ACTIVE and `as_of` falls within the policy period."""
        return (
            self.renewal_status.upper() == "ACTIVE"
            and self.policy_start_date <= as_of <= self.policy_end_date
        )

    # ── Coverage (Phase 2C) ──────────────────────────────────────────────────

    @property
    def per_claim_limit(self) -> Decimal:
        return Decimal(str(self._data["coverage"]["per_claim_limit"]))

    @property
    def annual_opd_limit(self) -> Decimal:
        return Decimal(str(self._data["coverage"]["annual_opd_limit"]))

    def get_category_terms(self, category: ClaimCategory) -> CategoryTerms:
        """
        The full opd_categories.<category> block, typed. Matches
        `has_category`'s case-insensitive lookup (opd_categories keys are
        lowercase, unlike document_requirements' uppercase keys — a known
        inconsistency in the protected source file, see `has_category`).
        Returns an all-defaults (`covered=False`) CategoryTerms if the
        category isn't present, rather than raising — PolicyEngine decides
        what an absent/uncovered category means, this is read-only access.
        """
        opd_categories: Dict[str, Any] = self._data.get("opd_categories", {})
        raw = next((v for k, v in opd_categories.items() if k.lower() == category.value.lower()), None)
        if raw is None:
            return CategoryTerms()

        def _decimal(key: str) -> Optional[Decimal]:
            return Decimal(str(raw[key])) if key in raw else None

        return CategoryTerms(
            covered=bool(raw.get("covered", False)),
            sub_limit=_decimal("sub_limit"),
            copay_percent=float(raw.get("copay_percent", 0.0)),
            network_discount_percent=float(raw.get("network_discount_percent", 0.0)),
            requires_prescription=bool(raw.get("requires_prescription", False)),
            requires_pre_auth=bool(raw.get("requires_pre_auth", False)),
            pre_auth_threshold=_decimal("pre_auth_threshold"),
            high_value_tests_requiring_pre_auth=list(raw.get("high_value_tests_requiring_pre_auth", [])),
            branded_drug_copay_percent=(
                float(raw["branded_drug_copay_percent"]) if "branded_drug_copay_percent" in raw else None
            ),
            generic_mandatory=bool(raw.get("generic_mandatory", False)),
            requires_dental_report=bool(raw.get("requires_dental_report", False)),
            covered_procedures=list(raw.get("covered_procedures", [])),
            excluded_procedures=list(raw.get("excluded_procedures", [])),
            covered_items=list(raw.get("covered_items", [])),
            excluded_items=list(raw.get("excluded_items", [])),
            requires_registered_practitioner=bool(raw.get("requires_registered_practitioner", False)),
            max_sessions_per_year=(
                int(raw["max_sessions_per_year"]) if "max_sessions_per_year" in raw else None
            ),
            covered_systems=list(raw.get("covered_systems", [])),
        )

    # ── Waiting Periods / Exclusions / Pre-Authorization (Phase 2C) ─────────

    @property
    def waiting_periods(self) -> WaitingPeriods:
        raw = self._data["waiting_periods"]
        return WaitingPeriods(
            initial_waiting_period_days=int(raw["initial_waiting_period_days"]),
            pre_existing_conditions_days=int(raw["pre_existing_conditions_days"]),
            specific_conditions={k: int(v) for k, v in raw.get("specific_conditions", {}).items()},
        )

    @property
    def exclusions(self) -> Exclusions:
        raw = self._data["exclusions"]
        return Exclusions(
            conditions=list(raw.get("conditions", [])),
            dental_exclusions=list(raw.get("dental_exclusions", [])),
            vision_exclusions=list(raw.get("vision_exclusions", [])),
        )

    @property
    def pre_authorization(self) -> PreAuthorizationRules:
        raw = self._data["pre_authorization"]
        return PreAuthorizationRules(
            required_for=list(raw.get("required_for", [])),
            validity_days=int(raw["validity_days"]),
        )

    # ── Network Hospitals (Phase 2C) ─────────────────────────────────────────

    @property
    def network_hospitals(self) -> List[str]:
        return list(self._data.get("network_hospitals", []))

    def is_network_hospital(self, hospital_name: Optional[str]) -> Optional[bool]:
        """
        Normalized (case/whitespace/common-punctuation-insensitive) match
        against network_hospitals. Returns None — not False — when
        `hospital_name` itself is unknown, since "not a network hospital"
        and "we don't know what hospital this is" are different findings
        (see docs/architecture.md "Policy Evaluation" for why PolicyEngine
        never assumes network status for an ambiguous name). Deliberately
        NOT fuzzy matching — exact match after normalization only.
        """
        if not hospital_name:
            return None
        target = _normalize_facility_name(hospital_name)
        return any(_normalize_facility_name(h) == target for h in self.network_hospitals)

    # ── Fraud Thresholds (Phase 2C) ──────────────────────────────────────────

    @property
    def fraud_thresholds(self) -> FraudThresholds:
        raw = self._data["fraud_thresholds"]
        return FraudThresholds(
            same_day_claims_limit=int(raw["same_day_claims_limit"]),
            monthly_claims_limit=int(raw["monthly_claims_limit"]),
            high_value_claim_threshold=Decimal(str(raw["high_value_claim_threshold"])),
            auto_manual_review_above=Decimal(str(raw["auto_manual_review_above"])),
            fraud_score_manual_review_threshold=float(raw["fraud_score_manual_review_threshold"]),
        )


def _normalize_facility_name(name: str) -> str:
    """Case/whitespace/common-punctuation-insensitive comparison key for
    hospital name matching. Deliberately narrow — strips only the
    punctuation Indian hospital names commonly vary by (periods, commas,
    extra spaces), not a general fuzzy-match algorithm."""
    import re

    cleaned = re.sub(r"[.,]", "", name)
    return re.sub(r"\s+", " ", cleaned.strip()).lower()


@lru_cache(maxsize=1)
def get_policy_repository(policy_file_path: str = "policy_terms.json") -> PolicyRepository:
    """
    Cached singleton accessor, mirroring get_settings()'s pattern. Tests
    that need a fresh instance should call get_policy_repository.cache_clear().
    """
    return PolicyRepository(policy_file_path)
