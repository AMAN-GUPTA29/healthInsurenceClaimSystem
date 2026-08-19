"""
PolicyEngine — Phase 2C, pipeline stage 5.

The DETERMINISTIC authority on all policy rules. Loads nothing itself —
reads everything through the injected PolicyRepository (policy_terms.json,
never hardcoded).

Rules:
- The LLM is NEVER the final authority on policy decisions. PolicyEngine
  makes no AI calls at all (ai_provider=None, like ClaimValidationAgent/
  CrossDocumentValidationAgent) — it only reads what DocumentExtractionAgent
  (Phase 2B) already extracted.
- Answers "what does the policy say?" — never "how much is payable?"
  (FinancialCalculationService) and never "is this claim approved?"
  (DecisionGenerationAgent, Phase 2D). See ARCHITECTURE.docx "7. Policy,
  Financial & Fraud Logic" for the full three-way separation.
- Every check produces a structured, explainable PolicyRuleFinding —
  never a giant free-form paragraph. See COMPONENT_CONTRACTS.docx
  "7. Policy Engine".
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from app.domain.models import Claim, ClaimCategory, DocumentType
from app.domain.policy_evaluation import (
    LineItemPolicyFinding,
    PolicyEvaluationResult,
    PolicyRuleFinding,
    PolicyRuleStatus,
)
from app.policy.policy_repository import CategoryTerms, PolicyRepository

# Hand-curated significant-keyword extraction for policy_terms.json's
# general `exclusions.conditions` phrases (full descriptive sentences, not
# single words) — see docs/tradeoffs.md "Diagnosis/Exclusion Normalization"
# for why naive whole-phrase substring matching doesn't work here (the
# phrases essentially never appear verbatim in extracted diagnosis text)
# and why per-word matching is too loose ("and"/"or" match anything).
# These keys are the exact strings from policy_terms.json — never modify
# that file; this table only maps its values to matchable keywords.
_EXCLUSION_KEYWORDS: Dict[str, List[str]] = {
    "Self-inflicted injuries": ["self-inflicted", "self inflicted", "suicide attempt"],
    "War or nuclear hazard": ["war injury", "nuclear hazard", "nuclear exposure"],
    "Substance abuse treatment": [
        "substance abuse", "de-addiction", "deaddiction", "alcohol dependence", "drug dependence",
    ],
    "Experimental treatments": ["experimental treatment", "investigational treatment"],
    "Infertility and assisted reproduction": ["infertility", "ivf", "assisted reproduction"],
    "Obesity and weight loss programs": ["obesity", "weight loss program", "weight-loss program"],
    "Bariatric surgery": ["bariatric"],
    "Cosmetic or aesthetic procedures": ["cosmetic", "aesthetic procedure"],
    "Vaccination (non-medically necessary)": ["vaccination"],
    "Health supplements and tonics": ["supplement", "tonic"],
}

# Specific-condition synonyms/abbreviations that a plain substring match on
# the policy_terms.json key wouldn't catch (e.g. "T2DM" doesn't contain
# "diabetes"). The key substring itself (with underscores -> spaces) is
# always checked first and needs no entry here — "diabetes mellitus"
# already matches "diabetes" as a substring without this table; this only
# adds the cases that wouldn't.
_CONDITION_ALIASES: Dict[str, List[str]] = {
    "diabetes": ["t2dm", "t1dm", "dm type"],
    "hypertension": ["htn"],
    "thyroid_disorders": ["hypothyroidism", "hyperthyroidism"],
    "joint_replacement": ["knee replacement", "hip replacement", "tkr", "thr"],
    "maternity": ["pregnancy", "antenatal", "delivery"],
    "mental_health": ["depression", "anxiety disorder", "psychiatric"],
    "obesity_treatment": ["obesity", "bariatric"],
}


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def _word_boundary_contains(text: str, phrase: str) -> bool:
    """
    Whole-word/phrase substring match — `phrase` must appear in `text` at a
    word boundary on both sides, so "hernia" does NOT match inside
    "herniation" (found live: TC007's "Suspected Lumbar Disc Herniation"
    was incorrectly matching the unrelated `hernia` specific-condition
    waiting period before this fix — exactly the false-positive class the
    assignment's own brief warned against). Plain substring containment
    (`phrase in text`) is still used deliberately in a few places where a
    *sub*-phrase match is wanted (e.g. line-item vs. policy-phrase
    matching in `_match_short_phrases`, which already checks both
    directions) — this helper is for single-word/short-phrase keys where
    an accidental prefix/suffix match is the real risk.
    """
    pattern = r"\b" + re.escape(_normalize_text(phrase)) + r"\b"
    return re.search(pattern, _normalize_text(text)) is not None


def _collect_clinical_text(claim: Claim) -> str:
    """
    Every diagnosis/treatment/procedure string DocumentExtractionAgent
    (Phase 2B) extracted, joined into one lowercase-matchable blob. Empty
    if extraction hasn't run or found nothing — callers must treat that as
    "no signal", never as "condition absent" (see waiting-period/exclusion
    checks, which report NOT_APPLICABLE rather than a false PASSED when
    there's genuinely nothing to check against).
    """
    if claim.extraction_result is None:
        return ""
    parts: List[str] = []
    for envelope in claim.extraction_result.extractions:
        extraction = envelope.extraction
        for field_name in ("diagnosis", "treatment", "procedure", "procedure_or_treatment"):
            value = getattr(extraction, field_name, None)
            if value:
                parts.append(value)
    return " ".join(parts)


def _resolve_hospital_name(claim: Claim) -> Optional[str]:
    """Prefer the AI-extracted hospital_name (from the actual bill content,
    Phase 2B) over the member-declared submission.hospital_name (an
    optional form field, not verified against any document)."""
    if claim.extraction_result is not None:
        for envelope in claim.extraction_result.extractions:
            name = getattr(envelope.extraction, "hospital_name", None)
            if name:
                return name
    return claim.submission.hospital_name


class PolicyEngine:
    """Deterministic policy rule evaluator. See module docstring."""

    def __init__(self, *, policy_repository: PolicyRepository) -> None:
        self._policy_repository = policy_repository

    async def evaluate(self, claim: Claim) -> PolicyEvaluationResult:
        submission = claim.submission
        category = submission.claim_category
        terms = self._policy_repository.get_category_terms(category)
        findings: List[PolicyRuleFinding] = []
        confidence = 1.0

        findings.append(self._check_policy_id(submission.policy_id))
        findings.append(self._check_policy_active(submission.treatment_date))
        findings.append(self._check_category_covered(category, terms))
        findings.append(self._check_submission_deadline(submission.treatment_date, claim.created_at))
        findings.append(self._check_minimum_amount(submission.claimed_amount))
        findings.append(self._check_per_claim_limit(submission.claimed_amount))
        findings.append(self._check_annual_limit(submission.ytd_claims_amount, submission.claimed_amount))
        findings.append(self._check_sub_limit(submission.claimed_amount, terms))
        findings.append(self._informational_copay(category, terms))

        hospital_name = _resolve_hospital_name(claim)
        is_network, network_finding = self._check_network(hospital_name)
        findings.append(network_finding)
        # Confidence impact deferred — see below, after exclusion/waiting-
        # period/pre-auth/per-claim-limit are all known: an unresolvable
        # hospital name only matters if network status could plausibly
        # change the payable amount.

        wp_findings, waiting_period_applies, wp_confidence = self._check_waiting_periods(claim)
        findings.extend(wp_findings)
        confidence = min(confidence, wp_confidence)

        excl_findings, exclusion_applies = self._check_exclusions(claim, category, terms)
        findings.extend(excl_findings)

        preauth_finding, requires_preauth, preauth_provided = self._check_pre_authorization(claim, terms)
        findings.append(preauth_finding)

        findings.append(self._check_prescription_requirement(claim, terms))
        if category == ClaimCategory.DENTAL:
            findings.append(self._check_dental_report_requirement(claim, terms))
        if category == ClaimCategory.ALTERNATIVE_MEDICINE:
            findings.append(self._check_registered_practitioner(claim, terms))
            findings.append(self._check_session_limit(terms))

        line_item_findings = self._evaluate_line_items(claim, category, terms)
        if any(f.excluded for f in line_item_findings):
            exclusion_applies = True

        # Phase 3 correctness fix: an unresolvable hospital name (the
        # NETWORK_HOSPITAL WARNING) only degrades confidence when network
        # status could plausibly change the payable amount — i.e. this
        # category actually has a network discount, AND the claim isn't
        # already headed for a claim-level rejection unrelated to money
        # (a rejected claim never reaches a network-discount calculation
        # at all). Mirrors DecisionGenerationAgent's own claim-level-
        # rejection preconditions (Rules 2-5.5) so the two never disagree
        # about what "irrelevant to the outcome" means. See TC012, whose
        # own confidence_score expectation (above 0.90) is unreachable
        # without this: an obesity-exclusion rejection has nothing to do
        # with which hospital it happened at.
        would_reject_regardless_of_network = (
            (exclusion_applies and not line_item_findings)
            or waiting_period_applies
            or (requires_preauth and not preauth_provided)
            or (
                not line_item_findings
                and self._policy_repository.per_claim_limit is not None
                and submission.claimed_amount > self._policy_repository.per_claim_limit
            )
        )
        if (
            network_finding.status == PolicyRuleStatus.WARNING
            and terms.network_discount_percent
            and not would_reject_regardless_of_network
        ):
            confidence = min(confidence, 0.6)

        passed_rules = [f.rule for f in findings if f.status == PolicyRuleStatus.PASSED]
        failed_rules = [f.rule for f in findings if f.status == PolicyRuleStatus.FAILED]
        warnings = [f"{f.rule}: {f.evidence}" for f in findings if f.status == PolicyRuleStatus.WARNING]

        return PolicyEvaluationResult(
            covered=terms.covered,
            coverage_category=category,
            findings=findings,
            passed_rules=passed_rules,
            failed_rules=failed_rules,
            warnings=warnings,
            requires_pre_authorization=requires_preauth,
            pre_authorization_provided=preauth_provided,
            waiting_period_applies=waiting_period_applies,
            exclusion_applies=exclusion_applies,
            is_network_hospital=is_network,
            sub_limit=terms.sub_limit,
            per_claim_limit=self._policy_repository.per_claim_limit,
            annual_opd_limit=self._policy_repository.annual_opd_limit,
            copay_percent=terms.copay_percent,
            network_discount_percent=terms.network_discount_percent,
            line_item_findings=line_item_findings,
            confidence=confidence,
        )

    # ── A. Policy validity ───────────────────────────────────────────────────

    def _check_policy_id(self, submitted_policy_id: str) -> PolicyRuleFinding:
        expected = self._policy_repository.policy_id
        if submitted_policy_id == expected:
            return PolicyRuleFinding(
                rule="POLICY_ID_MATCH", status=PolicyRuleStatus.PASSED,
                evidence=f"policy_id '{submitted_policy_id}' matches the configured policy.",
            )
        return PolicyRuleFinding(
            rule="POLICY_ID_MATCH", status=PolicyRuleStatus.FAILED,
            evidence=f"policy_id '{submitted_policy_id}' does not match configured policy '{expected}'.",
        )

    def _check_policy_active(self, treatment_date: date) -> PolicyRuleFinding:
        active = self._policy_repository.is_policy_active(treatment_date)
        evidence = (
            f"renewal_status={self._policy_repository.renewal_status}, "
            f"policy period {self._policy_repository.policy_start_date}..{self._policy_repository.policy_end_date}, "
            f"treatment_date={treatment_date}."
        )
        return PolicyRuleFinding(
            rule="POLICY_ACTIVE",
            status=PolicyRuleStatus.PASSED if active else PolicyRuleStatus.FAILED,
            evidence=evidence,
        )

    # ── B. Category coverage ─────────────────────────────────────────────────

    def _check_category_covered(self, category: ClaimCategory, terms: CategoryTerms) -> PolicyRuleFinding:
        rule = f"{category.value}_COVERED"
        evidence = f"opd_categories.{category.value.lower()}.covered = {terms.covered}"
        return PolicyRuleFinding(
            rule=rule, status=PolicyRuleStatus.PASSED if terms.covered else PolicyRuleStatus.FAILED, evidence=evidence,
        )

    # ── C. Submission deadline ───────────────────────────────────────────────

    def _check_submission_deadline(self, treatment_date: date, submitted_at: datetime) -> PolicyRuleFinding:
        """
        Uses the claim's actual submission timestamp (Claim.created_at),
        not a hardcoded 30 — deadline_days_from_treatment is read from
        policy_terms.json. Note: every assignment fixture is dated 2024,
        evaluated against a real system clock years later, so this will
        legitimately report FAILED for all of them — an honest finding,
        not a bug: unlike Phase 2A's validation stages, PolicyEngine's
        findings don't halt the pipeline — Phase 2D decides what a
        failed deadline means.
        """
        deadline_days = self._policy_repository.submission_deadline_days
        elapsed_days = (submitted_at.date() - treatment_date).days
        within_deadline = elapsed_days <= deadline_days
        evidence = (
            f"Submitted {elapsed_days} day(s) after treatment_date ({treatment_date}); "
            f"policy allows {deadline_days} days."
        )
        return PolicyRuleFinding(
            rule="SUBMISSION_DEADLINE",
            status=PolicyRuleStatus.PASSED if within_deadline else PolicyRuleStatus.FAILED,
            evidence=evidence,
            details={"elapsed_days": elapsed_days, "deadline_days": deadline_days},
        )

    # ── D. Minimum claim amount ──────────────────────────────────────────────

    def _check_minimum_amount(self, claimed_amount: Decimal) -> PolicyRuleFinding:
        minimum = self._policy_repository.minimum_claim_amount
        passed = claimed_amount >= minimum
        return PolicyRuleFinding(
            rule="MINIMUM_CLAIM_AMOUNT",
            status=PolicyRuleStatus.PASSED if passed else PolicyRuleStatus.FAILED,
            evidence=f"claimed_amount {claimed_amount} {'>=' if passed else '<'} minimum_claim_amount {minimum}.",
        )

    # ── E. Per-claim limit ───────────────────────────────────────────────────

    def _check_per_claim_limit(self, claimed_amount: Decimal) -> PolicyRuleFinding:
        limit = self._policy_repository.per_claim_limit
        passed = claimed_amount <= limit
        return PolicyRuleFinding(
            rule="PER_CLAIM_LIMIT",
            status=PolicyRuleStatus.PASSED if passed else PolicyRuleStatus.FAILED,
            evidence=f"claimed_amount {claimed_amount} {'<=' if passed else '>'} per_claim_limit {limit}.",
        )

    # ── F. Annual OPD limit ──────────────────────────────────────────────────

    def _check_annual_limit(self, ytd_claims_amount: Decimal, claimed_amount: Decimal) -> PolicyRuleFinding:
        annual_limit = self._policy_repository.annual_opd_limit
        projected = ytd_claims_amount + claimed_amount
        passed = projected <= annual_limit
        return PolicyRuleFinding(
            rule="ANNUAL_OPD_LIMIT",
            status=PolicyRuleStatus.PASSED if passed else PolicyRuleStatus.FAILED,
            evidence=(
                f"Year-to-date claims {ytd_claims_amount} + this claim {claimed_amount} = {projected}, "
                f"{'within' if passed else 'exceeds'} annual_opd_limit {annual_limit}."
            ),
        )

    # ── G. Category sub-limit ────────────────────────────────────────────────

    def _check_sub_limit(self, claimed_amount: Decimal, terms: CategoryTerms) -> PolicyRuleFinding:
        if terms.sub_limit is None:
            return PolicyRuleFinding(
                rule="SUB_LIMIT", status=PolicyRuleStatus.NOT_APPLICABLE,
                evidence="No sub_limit configured for this category.",
            )
        passed = claimed_amount <= terms.sub_limit
        return PolicyRuleFinding(
            rule="SUB_LIMIT",
            status=PolicyRuleStatus.PASSED if passed else PolicyRuleStatus.FAILED,
            evidence=f"claimed_amount {claimed_amount} {'<=' if passed else '>'} sub_limit {terms.sub_limit}.",
        )

    # ── H. Copay (informational — always PASSED, never a fail condition) ────

    def _informational_copay(self, category: ClaimCategory, terms: CategoryTerms) -> PolicyRuleFinding:
        return PolicyRuleFinding(
            rule="COPAY",
            status=PolicyRuleStatus.PASSED,
            evidence=f"opd_categories.{category.value.lower()}.copay_percent = {terms.copay_percent}",
            details={"copay_percent": terms.copay_percent},
        )

    # ── I. Network discount ──────────────────────────────────────────────────

    def _check_network(self, hospital_name: Optional[str]) -> Tuple[Optional[bool], PolicyRuleFinding]:
        is_network = self._policy_repository.is_network_hospital(hospital_name)
        if is_network is None:
            finding = PolicyRuleFinding(
                rule="NETWORK_HOSPITAL", status=PolicyRuleStatus.WARNING,
                evidence="No hospital/provider name could be determined — network discount status is "
                "unknown, not assumed either way.",
            )
        elif is_network:
            finding = PolicyRuleFinding(
                rule="NETWORK_HOSPITAL", status=PolicyRuleStatus.PASSED,
                evidence=f"'{hospital_name}' matched policy_terms.json network_hospitals.",
            )
        else:
            finding = PolicyRuleFinding(
                rule="NETWORK_HOSPITAL", status=PolicyRuleStatus.NOT_APPLICABLE,
                evidence=f"'{hospital_name}' is not a network hospital — standard (non-network) rates apply.",
            )
        return is_network, finding

    # ── J. Waiting periods ───────────────────────────────────────────────────

    def _check_waiting_periods(self, claim: Claim) -> Tuple[List[PolicyRuleFinding], bool, float]:
        findings: List[PolicyRuleFinding] = []
        waiting_period_applies = False
        confidence = 1.0
        wp = self._policy_repository.waiting_periods
        member = claim.member
        treatment_date = claim.submission.treatment_date

        if member is None:
            # Structurally shouldn't happen — PolicyEngine only runs after
            # ClaimValidationAgent already confirmed the member exists —
            # but never silently assume "no waiting period" if it does.
            findings.append(PolicyRuleFinding(
                rule="INITIAL_WAITING_PERIOD", status=PolicyRuleStatus.WARNING,
                evidence="Member identity unavailable — cannot evaluate waiting periods.",
            ))
            return findings, False, 0.5

        elapsed_days = (treatment_date - member.join_date).days
        initial_ok = elapsed_days >= wp.initial_waiting_period_days
        findings.append(PolicyRuleFinding(
            rule="INITIAL_WAITING_PERIOD",
            status=PolicyRuleStatus.PASSED if initial_ok else PolicyRuleStatus.FAILED,
            evidence=(
                f"Treatment occurred {elapsed_days} day(s) after member join date ({member.join_date}); "
                f"policy requires {wp.initial_waiting_period_days} days."
            ),
        ))
        if not initial_ok:
            waiting_period_applies = True

        # Pre-existing-condition waiting period: no structural signal
        # exists to determine whether a diagnosed condition is pre-existing
        # (this system collects no prior-diagnosis history) — flagged as a
        # warning rather than invented as PASSED or FAILED. See
        # docs/tradeoffs.md "Ambiguous Conditions".
        findings.append(PolicyRuleFinding(
            rule="PRE_EXISTING_CONDITION_WAITING_PERIOD", status=PolicyRuleStatus.WARNING,
            evidence=(
                f"Cannot determine whether the diagnosed condition is pre-existing (policy requires "
                f"{wp.pre_existing_conditions_days} days for pre-existing conditions) — no prior-diagnosis "
                "history is available to this system. Left for manual review."
            ),
        ))

        clinical_text = _collect_clinical_text(claim)
        if clinical_text:
            matched_condition = self._match_specific_condition(clinical_text, wp.specific_conditions)
            if matched_condition is not None:
                condition_key, required_days = matched_condition
                condition_ok = elapsed_days >= required_days
                findings.append(PolicyRuleFinding(
                    rule=f"WAITING_PERIOD_{condition_key.upper()}",
                    status=PolicyRuleStatus.PASSED if condition_ok else PolicyRuleStatus.FAILED,
                    evidence=(
                        f"Diagnosis text matched specific condition '{condition_key}'; treatment occurred "
                        f"{elapsed_days} day(s) after join date, policy requires {required_days} days for "
                        f"this condition."
                    ),
                    details={"matched_condition": condition_key},
                ))
                if not condition_ok:
                    waiting_period_applies = True
        # else: no clinical text extracted at all — specific-condition
        # waiting periods genuinely cannot be evaluated; no finding is
        # fabricated (silence here is honest, not a bug).

        return findings, waiting_period_applies, confidence

    @staticmethod
    def _match_specific_condition(
        clinical_text: str, specific_conditions: Dict[str, int]
    ) -> Optional[Tuple[str, int]]:
        """
        Returns (condition_key, required_days) for the first specific
        condition whose key (e.g. "diabetes") appears as a whole-word/
        whole-phrase match in `clinical_text` (see `_word_boundary_contains`
        — plain substring matching let "hernia" wrongly match inside
        "herniation"; fixed after finding this live against TC007's real
        diagnosis text), or via a documented alias (_CONDITION_ALIASES)
        for abbreviations a direct match would miss (e.g. "T2DM").
        Deliberately not fuzzy — see module docstring.
        """
        for condition_key, required_days in specific_conditions.items():
            key_phrase = condition_key.replace("_", " ")
            if _word_boundary_contains(clinical_text, key_phrase):
                return condition_key, required_days
            for alias in _CONDITION_ALIASES.get(condition_key, []):
                if _word_boundary_contains(clinical_text, alias):
                    return condition_key, required_days
        return None

    # ── K. Exclusions ────────────────────────────────────────────────────────

    def _check_exclusions(
        self, claim: Claim, category: ClaimCategory, terms: CategoryTerms
    ) -> Tuple[List[PolicyRuleFinding], bool]:
        findings: List[PolicyRuleFinding] = []
        exclusion_applies = False
        exclusions = self._policy_repository.exclusions
        clinical_text = _collect_clinical_text(claim)

        if not clinical_text:
            findings.append(PolicyRuleFinding(
                rule="EXCLUSION_CONDITIONS", status=PolicyRuleStatus.NOT_APPLICABLE,
                evidence="No extracted diagnosis/treatment text available to check against exclusions.",
            ))
            return findings, exclusion_applies

        matched_general = self._match_exclusion_keywords(clinical_text, exclusions.conditions)
        if matched_general:
            exclusion_applies = True
            findings.append(PolicyRuleFinding(
                rule="EXCLUSION_CONDITIONS", status=PolicyRuleStatus.FAILED,
                evidence=f"Diagnosis/treatment text matches excluded condition: '{matched_general}'.",
                details={"matched_exclusion": matched_general},
            ))
        else:
            findings.append(PolicyRuleFinding(
                rule="EXCLUSION_CONDITIONS", status=PolicyRuleStatus.PASSED,
                evidence="No match against exclusions.conditions.",
            ))

        if category == ClaimCategory.DENTAL and exclusions.dental_exclusions:
            matched = self._match_short_phrases(clinical_text, exclusions.dental_exclusions)
            if matched:
                exclusion_applies = True
                findings.append(PolicyRuleFinding(
                    rule="EXCLUSION_DENTAL", status=PolicyRuleStatus.FAILED,
                    evidence=f"Diagnosis/treatment text matches dental exclusion: '{matched}'.",
                ))

        if category == ClaimCategory.VISION and exclusions.vision_exclusions:
            matched = self._match_short_phrases(clinical_text, exclusions.vision_exclusions)
            if matched:
                exclusion_applies = True
                findings.append(PolicyRuleFinding(
                    rule="EXCLUSION_VISION", status=PolicyRuleStatus.FAILED,
                    evidence=f"Diagnosis/treatment text matches vision exclusion: '{matched}'.",
                ))

        return findings, exclusion_applies

    @staticmethod
    def _match_exclusion_keywords(clinical_text: str, exclusion_phrases: List[str]) -> Optional[str]:
        for phrase in exclusion_phrases:
            for keyword in _EXCLUSION_KEYWORDS.get(phrase, []):
                if _word_boundary_contains(clinical_text, keyword):
                    return phrase
        return None

    @staticmethod
    def _match_short_phrases(text: str, phrases: List[str]) -> Optional[str]:
        """
        For already-short/specific phrases (dental/vision exclusions, and
        line-item descriptions) — direct normalized substring match, no
        keyword table needed. Checks both directions (phrase-in-text and
        text-in-phrase) since a bill line item can be either more specific
        than the policy phrase ("Teeth Whitening - 1 session") or exactly
        as short ("Whitening").
        """
        normalized_text = _normalize_text(text)
        for phrase in phrases:
            normalized_phrase = _normalize_text(phrase)
            if normalized_phrase in normalized_text or normalized_text in normalized_phrase:
                return phrase
        return None

    # ── L. Pre-authorization ─────────────────────────────────────────────────

    def _check_pre_authorization(
        self, claim: Claim, terms: CategoryTerms
    ) -> Tuple[PolicyRuleFinding, bool, bool]:
        reasons: List[str] = []
        requires = terms.requires_pre_auth
        if requires:
            reasons.append("requires_pre_auth = true for this category")

        claimed_amount = claim.submission.claimed_amount
        if terms.pre_auth_threshold is not None and claimed_amount > terms.pre_auth_threshold:
            requires = True
            reasons.append(f"claimed_amount {claimed_amount} exceeds pre_auth_threshold {terms.pre_auth_threshold}")

        matched_tests: List[str] = []
        if terms.high_value_tests_requiring_pre_auth:
            clinical_text = _normalize_text(_collect_clinical_text(claim))
            matched_tests = [t for t in terms.high_value_tests_requiring_pre_auth if _normalize_text(t) in clinical_text]
            if matched_tests:
                requires = True
                reasons.append(f"high-value test(s) detected in extracted text: {', '.join(matched_tests)}")

        provided = any(doc.effective_type == DocumentType.PRE_AUTH_LETTER for doc in claim.documents)

        if not requires:
            return (
                PolicyRuleFinding(
                    rule="PRE_AUTHORIZATION", status=PolicyRuleStatus.NOT_APPLICABLE,
                    evidence="No pre-authorization required for this category/amount/procedure.",
                ),
                False,
                provided,
            )

        evidence = "; ".join(reasons)
        if provided:
            return (
                PolicyRuleFinding(
                    rule="PRE_AUTHORIZATION", status=PolicyRuleStatus.PASSED,
                    evidence=f"{evidence} — a PRE_AUTH_LETTER document was provided.",
                ),
                True,
                True,
            )
        return (
            PolicyRuleFinding(
                rule="PRE_AUTHORIZATION", status=PolicyRuleStatus.FAILED,
                evidence=f"{evidence} — no pre-authorization letter was found among the submitted documents.",
            ),
            True,
            False,
        )

    # ── Prescription / dental-report / practitioner / session requirements ──

    def _check_prescription_requirement(self, claim: Claim, terms: CategoryTerms) -> PolicyRuleFinding:
        if not terms.requires_prescription:
            return PolicyRuleFinding(
                rule="PRESCRIPTION_REQUIRED", status=PolicyRuleStatus.NOT_APPLICABLE,
                evidence="requires_prescription = false for this category.",
            )
        has_prescription = any(doc.effective_type == DocumentType.PRESCRIPTION for doc in claim.documents)
        return PolicyRuleFinding(
            rule="PRESCRIPTION_REQUIRED",
            status=PolicyRuleStatus.PASSED if has_prescription else PolicyRuleStatus.FAILED,
            evidence=(
                "A PRESCRIPTION document was submitted." if has_prescription
                else "requires_prescription = true, but no PRESCRIPTION document was found among the submitted documents."
            ),
        )

    def _check_dental_report_requirement(self, claim: Claim, terms: CategoryTerms) -> PolicyRuleFinding:
        if not terms.requires_dental_report:
            return PolicyRuleFinding(
                rule="DENTAL_REPORT_REQUIRED", status=PolicyRuleStatus.NOT_APPLICABLE,
                evidence="requires_dental_report = false.",
            )
        has_report = any(doc.effective_type == DocumentType.DENTAL_REPORT for doc in claim.documents)
        return PolicyRuleFinding(
            rule="DENTAL_REPORT_REQUIRED",
            status=PolicyRuleStatus.PASSED if has_report else PolicyRuleStatus.WARNING,
            evidence=(
                "A DENTAL_REPORT document was submitted." if has_report
                else "requires_dental_report = true, but no DENTAL_REPORT document was found — "
                "DENTAL's document_requirements lists it as optional, not required, so this is a "
                "warning, not a hard failure (see policy_terms.json document_requirements.DENTAL)."
            ),
        )

    def _check_registered_practitioner(self, claim: Claim, terms: CategoryTerms) -> PolicyRuleFinding:
        if not terms.requires_registered_practitioner:
            return PolicyRuleFinding(
                rule="REGISTERED_PRACTITIONER", status=PolicyRuleStatus.NOT_APPLICABLE,
                evidence="requires_registered_practitioner = false.",
            )
        registration_number: Optional[str] = None
        if claim.extraction_result is not None:
            for envelope in claim.extraction_result.extractions:
                reg = getattr(getattr(envelope.extraction, "doctor", None), "registration_number", None)
                if reg:
                    registration_number = reg
                    break
        if registration_number:
            return PolicyRuleFinding(
                rule="REGISTERED_PRACTITIONER", status=PolicyRuleStatus.PASSED,
                evidence=f"Practitioner registration number '{registration_number}' found on the prescription.",
            )
        return PolicyRuleFinding(
            rule="REGISTERED_PRACTITIONER", status=PolicyRuleStatus.WARNING,
            evidence="requires_registered_practitioner = true, but no doctor registration number was "
            "extracted from the submitted documents — cannot confirm registration status.",
        )

    def _check_session_limit(self, terms: CategoryTerms) -> PolicyRuleFinding:
        """
        max_sessions_per_year cannot be evaluated without a per-category
        session count across the policy year, which this system does not
        currently track (same limitation as the category sub_limit's
        annual-vs-per-claim ambiguity — see docs/tradeoffs.md). Reported as
        a warning, not fabricated as PASSED.
        """
        if terms.max_sessions_per_year is None:
            return PolicyRuleFinding(
                rule="SESSION_LIMIT", status=PolicyRuleStatus.NOT_APPLICABLE,
                evidence="No max_sessions_per_year configured for this category.",
            )
        return PolicyRuleFinding(
            rule="SESSION_LIMIT", status=PolicyRuleStatus.WARNING,
            evidence=f"Policy allows up to {terms.max_sessions_per_year} session(s)/year for this category, "
            "but this system does not yet track session counts across a policy year — cannot confirm "
            "whether this claim is within the limit.",
        )

    # ── Line-item-level exclusions (DENTAL / VISION only) ────────────────────

    def _evaluate_line_items(
        self, claim: Claim, category: ClaimCategory, terms: CategoryTerms
    ) -> List[LineItemPolicyFinding]:
        """
        Only DENTAL and VISION carry explicit covered/excluded procedure or
        item lists in policy_terms.json — every other category's
        exclusions are claim-level (diagnosis/treatment text), not
        itemized. Matches each bill line item's description against the
        excluded list first (normalized substring, either direction);
        anything not explicitly excluded is treated as not-excluded rather
        than requiring a positive covered-list match, since covered_*
        lists are illustrative examples, not necessarily exhaustive.
        """
        if category == ClaimCategory.DENTAL:
            excluded_list = terms.excluded_procedures
        elif category == ClaimCategory.VISION:
            excluded_list = terms.excluded_items
        else:
            return []

        if claim.extraction_result is None:
            return []

        findings: List[LineItemPolicyFinding] = []
        for envelope in claim.extraction_result.extractions:
            line_items = getattr(envelope.extraction, "line_items", None)
            if not line_items:
                continue
            for item in line_items:
                matched = self._match_short_phrases(item.description, excluded_list)
                findings.append(LineItemPolicyFinding(
                    description=item.description,
                    amount=item.amount,
                    excluded=matched is not None,
                    reason=matched,
                ))
        return findings
