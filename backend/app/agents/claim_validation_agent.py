"""
ClaimValidationAgent — the first pipeline stage.

Performs only the checks that must pass before it's even worth looking at
documents: does the member exist, does the policy match, is the category
structurally valid, is the claimed amount sane. Purely deterministic — no
AI calls, no vendor SDK, no coverage/waiting-period/exclusion logic (that
belongs to the PolicyEngine in a later phase).

Never raises for an expected validation failure — always returns a
ValidationResult (valid=False, errors=[...]). Only genuine infrastructure
failures (e.g. PolicyRepository failing to load) propagate as exceptions.
"""

from __future__ import annotations

from app.agents.base_agent import BaseAgent
from app.domain.models import ClaimSubmission
from app.domain.verification import ValidationIssue, ValidationResult
from app.policy.policy_repository import PolicyRepository


class ClaimValidationAgent(BaseAgent):
    def __init__(self, *, policy_repository: PolicyRepository, agent_name: str | None = None) -> None:
        super().__init__(ai_provider=None, agent_name=agent_name)
        self._policy_repository = policy_repository

    async def run(self, submission: ClaimSubmission) -> ValidationResult:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []

        member = self._policy_repository.get_member(submission.member_id)
        if member is None:
            errors.append(
                ValidationIssue(
                    code="MEMBER_NOT_FOUND",
                    field="member_id",
                    message=f"Member '{submission.member_id}' was not found on this policy.",
                    recoverable=False,
                )
            )

        if submission.policy_id != self._policy_repository.policy_id:
            errors.append(
                ValidationIssue(
                    code="POLICY_NOT_FOUND",
                    field="policy_id",
                    message=(
                        f"Policy '{submission.policy_id}' does not match the configured "
                        f"policy '{self._policy_repository.policy_id}'."
                    ),
                    recoverable=False,
                )
            )

        if not self._policy_repository.has_category(submission.claim_category):
            errors.append(
                ValidationIssue(
                    code="UNSUPPORTED_CATEGORY",
                    field="claim_category",
                    message=f"Claim category '{submission.claim_category.value}' is not defined in this policy.",
                    recoverable=False,
                )
            )

        # Note: treatment_date being non-future is already enforced by
        # ClaimSubmission's own Pydantic validator — nothing further to
        # check here structurally. A submission-deadline check
        # (LATE_SUBMISSION) is deliberately NOT implemented in Phase 2A:
        # it would require comparing treatment_date to wall-clock "today",
        # which is meaningless for the supplied test fixtures (all dated
        # 2024) evaluated against a much later system clock. Revisit once
        # claims carry a real `submitted_at` timestamp independent of
        # "now". See docs/AI_HANDOFF.md.

        minimum_amount = self._policy_repository.minimum_claim_amount
        if submission.claimed_amount < minimum_amount:
            errors.append(
                ValidationIssue(
                    code="BELOW_MINIMUM_AMOUNT",
                    field="claimed_amount",
                    message=(
                        f"Claimed amount {submission.claimed_amount} is below the policy's "
                        f"minimum claim amount of {minimum_amount}."
                    ),
                    recoverable=False,
                )
            )

        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings, member=member)
