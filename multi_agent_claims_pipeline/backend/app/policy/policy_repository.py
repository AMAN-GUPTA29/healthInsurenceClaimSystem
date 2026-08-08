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
from functools import lru_cache
from typing import Dict, List, Optional

from pydantic import BaseModel

from app.config.paths import resolve_source_file
from app.domain.errors import PolicyLoadError
from app.domain.models import ClaimCategory, DocumentType, Member, RelationshipType


class DocumentRequirement(BaseModel):
    """Required vs. optional document types for one claim category."""

    required: List[DocumentType]
    optional: List[DocumentType]


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


@lru_cache(maxsize=1)
def get_policy_repository(policy_file_path: str = "policy_terms.json") -> PolicyRepository:
    """
    Cached singleton accessor, mirroring get_settings()'s pattern. Tests
    that need a fresh instance should call get_policy_repository.cache_clear().
    """
    return PolicyRepository(policy_file_path)
