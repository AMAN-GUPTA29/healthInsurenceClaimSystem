# Trade-offs & Decisions

This document records significant trade-offs made during the project.

---

## Phase 0

### SQLite vs PostgreSQL
**Choice**: SQLite (dev default) with PostgreSQL-ready architecture  
**Reason**: This is a 2-3 day assignment. SQLite requires zero infrastructure. The `DATABASE_URL` env var and SQLAlchemy async engine make switching to PostgreSQL a 1-line change.  
**Trade-off**: SQLite has WAL limitations under concurrent writes. If the evaluation runner needs concurrency, use PostgreSQL.

### ABC vs Protocol for AIProvider
**Choice**: `abc.ABC` with `@abstractmethod`  
**Reason**: Makes missing implementations fail loudly at class definition time, not call time. Protocol would be more duck-typing friendly but less explicit for this use case.  
**Trade-off**: Slightly more boilerplate for simple providers.

### Anthropic `tool_use` for structured output
**Choice**: Force structured JSON via `tool_use`, not by prompting for JSON  
**Reason**: `tool_use` is more reliable. Prompting for JSON leads to occasional malformed responses that need retry logic.  
**Trade-off**: Slightly more tokens used; requires Anthropic-specific logic in the adapter.

### Pydantic v2 with `BaseSettings`
**Choice**: Pydantic v2 + `pydantic-settings`  
**Reason**: Better performance, stricter validation, built-in env var support.  
**Trade-off**: `pydantic-settings` is a separate package; some v1 patterns don't apply.

### Frontend: Inline styles vs CSS modules
**Choice**: Inline styles for Phase 0 shell  
**Reason**: No build step needed to see results; zero dependency on CSS tooling.  
**Trade-off**: Not scalable for large UIs. Phase 1 will use CSS modules or a design system.

---

## Phase 2A — Document Verification

### Document Classification — Structure/Issuer Over Specialty or Test-Type (Real Bugs Found Live)

**Reason — two related bugs found live**: `DocumentVerificationAgent`'s real-upload classification (`app/ai/prompts/document_verification.py`, via `AIProvider.analyze_document()`) originally gave the model no structural definition of what separates a bill from a report, or a lab-issued report from a clinical one — so it free-associated on medical vocabulary instead. (1) Itemized **bills** issued by a dental clinic or a diagnostics center were classified as that specialty's *report* type (`DENTAL_REPORT`/`DIAGNOSTIC_REPORT`) instead of `HOSPITAL_BILL`, because the model keyed off the specialty ("dental", "diagnostic") rather than the billing structure (itemized charges, `Bill No`, `Total Amount`, `Payment Mode`). (2) A laboratory's own report of an **imaging** test (an MRI, reported through an accredited lab's Sample ID/NABL-accreditation/TEST NAME-RESULT-UNIT-NORMAL RANGE structure) was classified as `DIAGNOSTIC_REPORT` instead of `LAB_REPORT`, because the model keyed off the test type ("MRI") rather than who issued the report and how it's tracked. Both blocked real DIAGNOSTIC/DENTAL claims at Document Verification (`missing_documents` reported the true requirement, `wrong_documents` reported the misclassified type).

**Fix**: the classification system prompt now defines each document type by structure/issuer, never by specialty or test type: BILL = itemized charges + billing/payment metadata (regardless of which specialty issued it); LAB_REPORT = laboratory-issued tracking/reporting structure (accredited-lab identity, Sample ID/Sample Date distinct from Report Date, TEST NAME/RESULT/UNIT/NORMAL RANGE, a registered pathologist) — regardless of which test it reports, imaging included; DIAGNOSTIC_REPORT = the same kind of clinical narrative *without* that lab-issued tracking structure. The taxonomy (`DocumentType` enum) and the required-document mapping (`PolicyRepository.get_document_requirements`) were both already correct and untouched — only the AI's classification *input* was wrong. See `tests/unit/test_document_classification_prompt.py` (prompt-content regression tests) and `tests/unit/test_document_verification_agent.py::TestBillVsReportClassificationOutcome` / `::TestLabReportVsDiagnosticReportClassificationOutcome` (consequence-level regression tests with mocked AI responses) — plus a manual live-Claude verification against the actual sample PDFs.

**Trade-off**: this is prompt guidance, not a hard classification rule — it improves a real model's structural reasoning but (like any LLM-based classification) cannot be proven correct for every possible document by a mocked/deterministic test suite alone; the unit tests lock in that the guidance text itself is present and generic (no test-case ID, filename, or keyword-only shortcut), not that a live model will always agree.

### No-Legible-Name Gap in Cross-Document Identity Validation

**Choice**: `CrossDocumentValidationAgent` used to silently PASS whenever zero uploaded documents had an AI-readable patient name at all — the reasoning was "nothing to cross-check," but that's different from "nothing to worry about": a claim whose documents genuinely belong to someone else, with a name too degraded, cropped, or absent for the AI to read, would sail through with no identity check having run at all, as long as the (also nameless) documents didn't disagree with *each other*. Now, when a real classification pass (`DocumentClassification.source == "ai"`) finds no legible name anywhere and the claim's member is known, the claim BLOCKS with a message asking for a document that clearly shows the patient's name — the same conservative "can't verify -> don't assume" stance already used for network-hospital matching above.

**Reason**: the existing document<->member check (added by the earlier identity-fix, see `app/agents/cross_document_validation_agent.py`) only ever ran when at least one document had a name to compare — a total absence of names skipped it entirely, which is the opposite of what "we can't verify identity" should mean for an insurance claim.

**Trade-off**: evaluation fixtures (`DocumentClassification.source == "fixture"`, from `DocumentInputAdapter`) are deliberately exempt — `test_cases.json` only populates `patient_name_on_doc` for the one official case that specifically tests identity matching (TC003), so a blanket enforcement would have blocked most of the other eleven official cases for a reason unrelated to what they're actually testing. This means the stricter check only actually fires on the real upload path (`POST /api/v1/claims`), not the official evaluation — intentional, since the official evaluation was never exercising real OCR/AI name extraction in the first place. See `tests/unit/test_cross_document_validation_agent.py::TestNoLegibleNameGapFix` and `tests/integration/test_claims_api.py::test_submit_claim_with_no_legible_patient_name_blocks_instead_of_silently_passing`.

---

## Phase 2C — Policy Engine, Financial Calculation & Fraud Analysis

### Financial Calculation Order
**Choice (revised in Phase 3 — see "Phase 3 Correctness Pass" below for the full story)**: eligible amount → network discount → remaining annual OPD allowance cap → copay deduction → payable amount. `sub_limit` and `per_claim_limit` are surfaced on the result for transparency but are **not** applied as payable-amount caps here — `per_claim_limit` is instead a whole-claim REJECT gate evaluated by `DecisionGenerationAgent` before any payable figure is used (see "Decision Precedence" below).
**Reason**: `test_cases.json`'s own official worked examples (TC006, TC008, TC010) settle what the assignment's literal "apply sub-limit and per-claim-limit" instruction left ambiguous: whether "apply" means *cap the payable amount* or *gate the whole claim*. Phase 2C originally read it as "cap," which reproduced the assignment's own generic rule list but numerically contradicted three of its twelve worked examples simultaneously — addressed in Phase 3, see below.
**Trade-off**: a claim that exceeds its category `sub_limit` but has no line-item exclusion and isn't rejected for exceeding `per_claim_limit` (e.g. a very large single-category claim under the global per-claim ceiling) pays out the full eligible amount uncapped by that category's own sub-limit. No official test case exercises this combination, so it's untested either way — but it follows directly from the same reading that reproduces all twelve official cases, rather than a special case invented for this one.

### Phase 3 Correctness Pass — Resolving TC006/TC008/TC010 (formerly a disclosed discrepancy)

Phase 2C/2D originally applied `sub_limit` and `per_claim_limit` as hard caps on `payable_amount`, and treated a `PER_CLAIM_LIMIT` policy failure as informational only (never triggering `REJECTED`). This reproduced the assignment's own literal 10-rule decision list but disagreed with `test_cases.json`'s own worked numbers for TC006 and TC010, and with TC008's expected *decision* outright — disclosed at the time as an accepted, unresolved discrepancy. A Phase 3 audit re-derived the actual reference data instead of accepting that discrepancy:

- **TC006** (dental, ₹8,000 eligible after excluding a ₹4,000 cosmetic line item, `per_claim_limit`=₹5,000): official `approved_amount` is ₹8,000 — **not** capped to ₹5,000, even though ₹8,000 (and the original ₹12,000 claimed amount) both exceed the global per-claim limit.
- **TC010** (network hospital, ₹4,500 claimed → ₹3,600 after 20% discount, CONSULTATION `sub_limit`=₹2,000): official `approved_amount` is ₹3,240 (₹3,600 × 0.9 copay) — **not** capped to ₹2,000 at any point, even though ₹3,600 exceeds the category sub-limit.
- **TC008** (consultation, ₹7,500 claimed, `per_claim_limit`=₹5,000, no line items): official decision is `REJECTED` (`PER_CLAIM_EXCEEDED`) — the **whole claim**, not a partial/capped payable amount.

Read together, these three cases are only mutually consistent under one interpretation: **`per_claim_limit` is a whole-claim reject gate, evaluated against the raw claimed amount, and only when the claim has no line-item-driven partial eligibility to fall back on instead. `sub_limit` is not a hard cap on payable amount at all in any official example.** TC006 and TC008 both have a raw claimed amount exceeding `per_claim_limit` — the only structural difference is that TC006 already has a lower, *trusted* eligible amount established by genuine line-item exclusion (cosmetic procedure), while TC008 has no line items and would otherwise be fully payable. That difference is exactly what separates "reject the whole claim" from "trust the itemized figure, don't re-gate it."

**Implementation**: `FinancialCalculationService` no longer applies `sub_limit`/`per_claim_limit` as caps (see "Financial Calculation Order" above). `DecisionGenerationAgent` gained a new precedence step (Rule 5.5, between pre-authorization and fraud manual-review): if the claimed amount exceeds `per_claim_limit`, AND no other claim-level rejection reason already applies (exclusion/waiting-period/pre-auth — this ordering matters, see TC012 below), AND the claim has no line-item-driven partial eligibility (`financial.eligible_amount == financial.claimed_amount`) — the whole claim is `REJECTED` with `PER_CLAIM_EXCEEDED`.

**Why the "no other rejection reason already applies" guard matters (TC012)**: TC012 (obesity exclusion) has a claimed amount (₹8,000) that also exceeds `per_claim_limit` (₹5,000), but `test_cases.json` expects `rejection_reasons: ["EXCLUDED_CONDITION"]` alone, not both. Checking `per_claim_limit` only when no earlier claim-level rejection has already fired reproduces this exactly, and is the generically correct behavior regardless of test IDs: once a claim is already being rejected for an unrelated reason, there's no additional value in also reporting a coincidental limit breach.

**This fix reproduces all twelve official cases exactly** — decision AND amount — with a single generic rule, not per-case branching. See `docs/eval-report.md` for the full 12/12 table and `app/evaluation/runner.py`/`scripts/run_eval.py` to reproduce it.

**What was deliberately NOT done**: no `if case_id == "TC008"` or member/amount-specific branching exists anywhere in `DecisionGenerationAgent`/`PolicyEngine`/`FinancialCalculationService`. The rule above is expressed purely in terms of `policy.per_claim_limit`, `financial.eligible_amount`/`claimed_amount`, and existing rejection-reason state — it would apply identically to a claim never seen in `test_cases.json`.

### Network-Hospital-Unknown Confidence Cap — Phase 3 Correctness Fix

**Problem found during the Phase 3 audit**: `PolicyEngine` capped its own `confidence` at 0.6 whenever a hospital name was unresolvable (`NETWORK_HOSPITAL: WARNING`), unconditionally — even for a claim that was already being rejected for reasons that have nothing to do with money (e.g. TC012's obesity exclusion), and even for categories with no network discount configured at all (e.g. DENTAL). This made TC012's own expected `confidence_score` ("above 0.90") unreachable even though nothing about that claim was actually degraded or uncertain in a way that mattered to its outcome.
**Fix**: the cap now only applies when network status could plausibly change the payable amount — the category has a non-zero `network_discount_percent`, AND the claim isn't already headed for a claim-level rejection for an unrelated reason (mirrors `DecisionGenerationAgent`'s own Rules 2-5.5 preconditions, reused rather than reinvented, so the two never disagree about what "irrelevant to the outcome" means). See `tests/unit/test_policy_engine.py::TestT_NetworkHospitalConfidenceRelevance`.

### Rounding
**Choice**: `Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` at every money-producing step (discount amount, copay amount) — never Python's default (banker's rounding, `ROUND_HALF_EVEN`), never truncation.
**Reason**: `ROUND_HALF_UP` ("round half away from zero") is the rounding convention members and finance teams actually expect (₹0.005 → ₹0.01, not down to ₹0.00 half the time depending on the preceding digit) — `ROUND_HALF_EVEN` is a good default for statistical aggregation, not for a single member-facing payable amount.
**Trade-off**: Verified explicitly in `tests/unit/test_financial_calculation_service.py::TestRounding` (`333.33 × 33% = 109.9989 → 110.00`, which `ROUND_HALF_EVEN` would also round to 110.00 in this specific case — the test intentionally checks a case where the two conventions could plausibly diverge, to lock in the intended behavior rather than relying on both conventions coincidentally agreeing).

### Network Hospital Matching
**Choice**: Case/whitespace/punctuation-insensitive **exact** match (`_normalize_facility_name()` on both sides) against `policy_terms.json`'s `network_hospitals` list — never fuzzy/similarity matching.
**Reason**: Same reasoning as Phase 2A's exact (not fuzzy) cross-document patient-name matching — a false-positive network match would apply a discount the hospital never agreed to and could misstate a payable amount; a false negative just means "treated as non-network," a safe, conservative default.
**Trade-off**: `"City Hospital"` vs. `"City Hospital Pvt Ltd"` would not match. `PolicyRepository.is_network_hospital()` returns `Optional[bool]` — `None` when the hospital name itself is unknown/empty (extraction never ran, or the field wasn't visible), which `PolicyEngine` reports as a `WARNING` finding, not a silent `False` — the caller can distinguish "definitely not network" from "we don't know."

### Diagnosis/Exclusion Normalization
**Choice**: Word-boundary regex matching (`_word_boundary_contains()`, `\b`-delimited) for specific-condition waiting periods and general exclusion keywords against free-text diagnosis/treatment/procedure fields extracted from documents — not naive `phrase in text` substring containment, and not full fuzzy/NLP matching either.
**Reason — a real bug found live**: naive substring containment on the word `"hernia"` matched inside `"Herniation"` when evaluating TC007's real extracted diagnosis text, "Suspected Lumbar Disc Herniation" — a spinal-disc condition entirely unrelated to the abdominal/inguinal hernia the policy's `specific_conditions.hernia` waiting period concerns. This is precisely the false-positive class the assignment brief warned against ("do not accidentally classify unrelated text as diabetes"). Found during manual scenario verification (not by the initial automated test suite, which happened not to exercise this exact word pair), fixed by requiring both edges of the match to be word boundaries, and a regression assertion was added to `tests/unit/test_policy_engine.py` to lock in that "Herniation" must never trigger the "hernia" waiting period while genuine hernia-diagnosis text still does.
**Trade-off**: Word-boundary matching is still not medical-NLP-grade (it won't catch a genuine synonym with zero shared substring, e.g. matching "T2DM" to "diabetes" relies on the separate, hand-curated `_CONDITION_ALIASES` table, not the matcher itself) and the exclusion-phrase keyword tables (`_EXCLUSION_KEYWORDS`) are hand-curated because `policy_terms.json`'s exclusion phrases are full descriptive sentences ("Obesity and weight loss programs") that essentially never appear verbatim in extracted clinical text — a real production system would likely want a proper medical-terminology/ICD-code mapping layer instead of hand-curated keyword lists. Dental/vision line-item matching (`_match_short_phrases`) deliberately keeps bidirectional plain substring matching rather than word-boundary matching — those phrases are already short, closed-vocabulary procedure names ("Whitening", "Teeth Whitening — 1 session"), not free-text diagnoses, so the false-positive risk that motivated the word-boundary fix doesn't apply the same way there.

### Ambiguous/Unverifiable Conditions
**Choice**: Several policy checks that the current data model has no way to verify definitively are recorded as `WARNING` findings, never silently passed (`PASSED`) or silently failed (`FAILED`).
**Reason/examples**:
- `PRE_EXISTING_CONDITION_WAITING_PERIOD` — no field anywhere captures "when was this condition first diagnosed" independent of the current claim, so this is always a `WARNING` ("insufficient data to determine pre-existing status"), never a pass or a fail.
- Dental-report requirement — `DENTAL_REPORT` is an optional document per `policy_terms.json`'s `document_requirements`, so its absence is a `WARNING`, not a `FAILED` coverage rule.
- Session-limit checks (`max_sessions_per_year`, alternative-medicine categories) — no session-count tracking exists across claims yet, so this is always a `WARNING`, never a computed pass/fail.
**Trade-off**: A human reviewer sees these as explicit, named gaps in what the system could verify — the honest alternative to guessing an answer the data can't actually support. Revisit once the domain model gains the fields these checks would need (diagnosis-first-noted date, per-member session counters).

### Fraud Counting Semantics
**Choice**: `same_day_claim_count`/`monthly_claim_count` always include the **current** claim being evaluated, not just prior history — a claim with zero prior history still counts as 1, never 0. `requires_manual_review` is triggered by same-day/monthly pattern breaches or crossing `auto_manual_review_above`, but **not** by `is_high_value` alone (high-value alone is `MEDIUM` risk, informational).
**Reason**: "How many claims exist for this member on this day/in this month, including the one just submitted" is the number that's actually meaningful against a policy threshold like `same_day_claims_limit=2` — comparing prior-only history against the same threshold would silently allow one more claim through than the threshold intends. Decoupling `is_high_value` from `requires_manual_review` avoids over-triggering manual review for every large-but-ordinary claim (e.g. a legitimate high-value hospitalization) — high value alone is a softer signal than an actual behavioral-pattern breach.
**Trade-off**: This is a documented assumption, not something `policy_terms.json` states explicitly either way — `tests/unit/test_fraud_analysis_agent.py::TestSameDayClaimsThreshold::test_current_claim_is_counted_exactly_once` locks in the "always +1" behavior so a future change to this assumption fails loudly instead of silently.

### Bill Amount Reconciliation
**Choice**: When a hospital bill's line items don't sum to its own reported total, or the reported total disagrees with the claimed/eligible amount by more than a ₹1.00 tolerance, `FinancialCalculationService` records a `warnings` entry naming both values — it never silently "corrects" either one or picks a winner.
**Reason**: An AI-extracted bill total and a member-submitted claimed amount are both *evidence*, not authoritative inputs the system should reconcile on the calling code's behalf — silently substituting one for the other could mask a genuine data-entry error or a real discrepancy worth a human's attention, exactly the kind of invisible "fix" the assignment's transparency goals warn against.
**Trade-off**: `payable_amount` is still computed from the resolved eligible-amount base (claimed amount, or the itemized non-excluded sum for dental/vision) even when a reconciliation warning fires — the claim isn't blocked or held up by a bill total that doesn't add up, it's just flagged for a human to notice via `FinancialBreakdown.warnings`.

---

## Phase 2D — Decision Generation & Explanation

### Decision Precedence

**Choice**: A fixed, ordered precedence in `DecisionGenerationAgent`, derived directly from `assignment.md`, `test_cases.json`'s 12 official expectations, and `policy_terms.json` — never invented, never LLM-chosen:

1. `policy_evaluation_result is None` or `financial_calculation_result is None` → `MANUAL_REVIEW` (insufficient evidence for a safe automatic decision), `approved_amount=None`.
2. Claim-level (non-itemized) policy exclusion (`exclusion_applies=True` **and** `line_item_findings` is empty), waiting period (`waiting_period_applies=True`), and missing required pre-authorization (`requires_pre_authorization=True` and `not pre_authorization_provided`) are each checked and **collected together**, not first-match-wins — any one or more present → `REJECTED` with every applicable `RejectionReason`.
3. (Rule 5.5, Phase 3) If none of those fired, and the claimed amount exceeds `per_claim_limit`, and the claim has no line-item-driven partial eligibility (`eligible_amount == claimed_amount`) → `REJECTED` with `PER_CLAIM_EXCEEDED`. See "Phase 3 Correctness Pass" above for the full derivation — this is the rule that reconciles TC006/TC008/TC010.
4. If none of the above fired and `fraud_analysis_result.requires_manual_review` → `MANUAL_REVIEW`, but `approved_amount` still reflects `financial.payable_amount` since it's reliable.
5. `financial.payable_amount <= 0` → `REJECTED`.
6. `financial.eligible_amount < financial.claimed_amount` (genuine line-item exclusion, DENTAL/VISION itemized bills only) → `PARTIAL`; otherwise → `APPROVED`.
7. If overall confidence is still below the low-confidence threshold (0.5) at this point → downgrade to `MANUAL_REVIEW`, but keep the reliable `approved_amount` (per the spec: use the existing financial result if one exists, rather than discarding it).

**Reason for the claim-level-vs-itemized exclusion split (step 2)**: `PolicyEngine` sets `exclusion_applies=True` in two structurally different situations — a whole-claim condition/procedure exclusion (TC012, obesity treatment: no itemized bill, the entire claim is excluded) and a DENTAL/VISION itemized bill where only *one* line item happens to be excluded (TC006: root canal covered, teeth whitening excluded). Treating both the same way (`exclusion_applies=True` ⇒ `REJECTED`) would have rejected TC006 outright, contradicting its own official expected decision (`PARTIAL`, ₹8000 approved). The `line_item_findings` list is exactly the structural signal that distinguishes the two cases — non-empty means there's a legitimate partial-approval story to tell instead of a blanket rejection.

**Reason for basing `PARTIAL` vs. `APPROVED` on `eligible_amount` vs. `claimed_amount`, not `payable_amount` vs. `claimed_amount`**: copay and network discount both reduce `payable_amount` below `claimed_amount` for a completely normal, fully-covered claim (TC004: ₹1500 claimed, ₹1350 payable after 10% copay; TC010: discount + copay). Using a raw amount comparison for the `PARTIAL` test would have misclassified every claim with any copay or discount as `PARTIAL`. `eligible_amount` is `FinancialCalculationService`'s own field for "how much of the *claimed content* is even eligible before discount/limits/copay" — it only drops below `claimed_amount` via genuine line-item exclusion, which is the correct signal for "some of what you claimed for isn't covered," as opposed to "your claim is covered, subject to normal plan terms."

**TC006/TC008/TC010 — resolved in Phase 3, no longer a discrepancy**: an earlier version of this document recorded these as an accepted, disclosed mismatch (TC008 reaching `APPROVED` instead of the official `REJECTED`, and TC006/TC010's amounts differing from their official figures). A Phase 3 audit revisited the assumption behind that mismatch — that `per_claim_limit`/`sub_limit` are payable caps — and found it was the wrong reading of the same evidence: all three cases are reproduced exactly by treating `per_claim_limit` as a whole-claim reject gate (Rule 3 above) rather than a cap, and `sub_limit` as informational only. See "Phase 3 Correctness Pass" above for the full derivation and `docs/eval-report.md` for the resulting 12/12 table. Nothing about this fix is test-ID-specific: `DecisionGenerationAgent` has no knowledge that "TC008" exists, only `policy.per_claim_limit`, `financial.eligible_amount`/`claimed_amount`, and existing rejection-reason state.

### Decision Confidence Strategy

**Choice**: `confidence = max(0.0, min(available stage confidences) - 0.15 × count(missing/degraded stages))`. "Available stage confidences" means `document_verification_result.confidence`, `cross_document_validation_result.confidence`, `extraction_result.confidence`, `policy_evaluation_result.confidence`, `financial_calculation_result.confidence`, and `fraud_analysis_result.confidence` — each included only if that stage actually produced a result. A stage that produced **no result at all** (missing/failed) is *not* included in the `min()` (there is no number to include) but instead adds one unit of penalty — treated as strictly worse than a merely-low-confidence result, since a missing result means zero evidence rather than uncertain evidence.

**Reason**: this reuses the exact "never fabricate, minimum wins" convention already established for `ClaimExtractionResult.confidence` (Phase 2B) and `PolicyEvaluationResult`'s own network-hospital-unknown cap (Phase 2C) — Phase 2D doesn't invent a new philosophy, it extends the existing one up to the decision layer. The additional missing-stage penalty exists because "we have a 0.6-confidence policy finding" and "we have no policy finding at all" are meaningfully different situations that a single `min()` over available numbers alone cannot distinguish (a `None` simply isn't there to compare).

**Threshold calibration**: `_LOW_CONFIDENCE_THRESHOLD = 0.5`, chosen specifically so that a *single* degraded non-critical component never crosses it alone. Worked example (TC011): Policy/Financial both clean (confidence ≈1.0), Fraud Analysis missing (simulated failure) → one missing-stage penalty (−0.15) → confidence ≈0.85, comfortably `APPROVED`, not downgraded — matching TC011's own official expectation (`APPROVED`, with reduced confidence, not `MANUAL_REVIEW`). Two simultaneously degraded/low-confidence stages (e.g. an unrecognised hospital name capping Policy at 0.6, plus a document-extraction failure) can cross the threshold, which is intentional — that combination genuinely represents "we have meaningfully less to go on," not a single incidental gap.

**Explicit disclaimer**: this is an **operational** confidence score for triaging which claims need a human look, not a statistically calibrated probability of correctness. No claim is made that a 0.85-confidence decision is right 85% of the time. Calling it anything more precise than "operational" would overstate what a handful of deterministic rule-of-thumb penalties can actually tell you.

### Fallback Explanation

**Choice**: When `ExplanationAgent`'s LLM call fails or returns an invalid response, the fallback `ExplanationResult` is built **entirely from fields already computed on `ClaimDecision`** — `member_facing_message`/`explanation` (both already deterministic, written by `DecisionGenerationAgent` itself as a safety net independent of whether Explanation ever runs), `financial_breakdown.calculation_steps` (as `deductions`), `rejection_reasons`/`fraud_signals` (as `key_reasons`), and `degraded_components` (as `warnings`) — never a generic "an error occurred" placeholder, and never any fact not already present elsewhere in the pipeline's own output.

**Reason**: assignment.md point 6 requires the system to "continue with whatever it has" on a component failure, not degrade to an unhelpful placeholder. Since `DecisionGenerationAgent` already writes a plain-language `explanation`/`member_facing_message` as part of producing the decision (see its own contract), `ExplanationAgent`'s fallback has real, decision-grounded prose to fall back to even with zero AI involvement — the LLM's job in the happy path is to make that prose *richer* (structured key reasons, itemized deductions, specific policy findings), not to be the only source of any explanation existing at all.

**Trade-off**: the fallback text is noticeably less rich than a successful AI explanation (no free-form synthesis across findings, no natural-language nuance) — `source=FALLBACK` and `degraded=True` are always present precisely so a caller (the UI, an ops reviewer) can tell the difference and not mistake terse deterministic prose for a fully-reasoned write-up.

### LLM Limitations (Explanation)

The explanation LLM is deliberately constrained to a narrow, low-risk role — see `app/ai/prompts/explanation.py`'s system prompt for the complete list of things it must never do (calculate/recalculate amounts, invent policy rules, override the decision, restate the approved amount as a different number, invent facts, claim a document was verified when it wasn't, claim fraud when none was flagged, expose internal implementation details). This is enforced at three layers, not just the prompt: (1) the evidence given to the model never includes anything it could use to "discover" a different decision (no raw documents, no unredacted internal state); (2) the response is validated through `ExplanationAIResponse` (Pydantic) before any of it is trusted; (3) even a fully valid response can never change `decision.decision`/`.approved_amount` — the pipeline only ever writes the LLM's *prose* fields (`explanation`, `member_facing_message`, `explanation_detail`) onto the claim, never re-derives the decision from anything the model returned. No test in this codebase — and no code path — allows the LLM's output to influence which of `APPROVED`/`PARTIAL`/`REJECTED`/`MANUAL_REVIEW` a claim receives.

### Assumptions

- **A "reliable financial figure" for `MANUAL_REVIEW`** is defined as "Financial Calculation produced *any* result" — the code does not additionally check `financial_calculation_result.confidence` before deciding whether to surface `approved_amount` on a `MANUAL_REVIEW` decision. A financial result with low confidence (e.g. from a bill-reconciliation warning) is still shown, on the reasoning that a human reviewer benefits from seeing the computed figure even if imperfect, rather than seeing nothing.
- **`manual_review_recommended` is decoupled from `decision == MANUAL_REVIEW`** — it is `True` whenever `degraded_components` is non-empty, *regardless* of the final decision (see TC011: `APPROVED` with `manual_review_recommended=True`). This lets the system express "this was still a safe automatic decision, but a human might want to double-check it anyway" without forcing every degraded-but-otherwise-clean claim into the `MANUAL_REVIEW` bucket.
- **`ClaimStatus.DECIDED`** (a Phase 0 enum value, unused until now) was chosen over inventing a new status for "Decision Generation ran and produced a result" — it already existed with exactly this meaning ("Processing lifecycle status... a final decision has been reached").
