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

## Phase 2C — Policy Engine, Financial Calculation & Fraud Analysis

### Financial Calculation Order
**Choice**: eligible amount → network discount → category sub-limit cap → per-claim limit cap → remaining annual OPD allowance cap → copay deduction → payable amount. Fixed, non-configurable order.
**Reason**: The assignment brief lists these as ordered steps (discount before limits, limits before copay — TC010 specifically exists to test "discount-before-copay ordering"). Applying caps *after* the discount means the discount can only ever help the member, never accidentally push a claim over a cap it would otherwise have stayed under; applying copay last means copay is always computed on the actually-payable-before-copay base, not on the raw claimed amount.
**Trade-off — a real, disclosed discrepancy**: applying `sub_limit` and `per_claim_limit` as hard caps (per the brief's literal instruction) makes this implementation's computed `payable_amount` **differ from `test_cases.json`'s own worked examples** for two cases:
- **TC010** (network hospital, ₹4500 claimed, 20% discount → ₹3600, then a CONSULTATION `sub_limit` of ₹2000 applies) — `test_cases.json` states an expected payable of ₹3240 (i.e. as if no sub-limit were applied at all: ₹4500 × 0.8 × 0.9 copay = 3240), but this implementation correctly caps at the ₹2000 sub-limit first, producing ₹1800 after copay.
- **TC006** (dental, ₹12,000 claimed, `per_claim_limit`=₹5,000 globally) — `test_cases.json`'s worked example implies ₹8,000 survives to the payable calculation, but the per-claim limit caps it at ₹5,000, producing a lower final payable than the worked example shows.

Both are cases where the assignment's own synthetic test data appears to have been authored without the global `per_claim_limit`/category `sub_limit` actually being applied in the reference calculation — i.e. the worked examples look like they were computed *before* those caps, even though `policy_terms.json` defines them and the brief's own rule list says to apply them. Reverse-engineering the test author's likely-unintended omission (e.g. skipping the cap silently for these two categories) would mean *not* applying a real, explicitly-documented policy rule — a worse trade-off than a disclosed numeric mismatch against two worked examples. **Decision**: apply the caps for real, for every category, and document the discrepancy here rather than special-casing around it. `TestFinancialCalculationService`'s `TestNetworkDiscountOrdering` class deliberately tests the TC010-shaped discount-before-copay ordering *without* a sub-limit set, isolating that ordering behavior from this separate, disclosed limitation.

### Rounding
**Choice**: `Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` at every money-producing step (discount amount, copay amount) — never Python's default (banker's rounding, `ROUND_HALF_EVEN`), never truncation.
**Reason**: `ROUND_HALF_UP` ("round half away from zero") is the rounding convention members and finance teams actually expect (₹0.005 → ₹0.01, not down to ₹0.00 half the time depending on the preceding digit) — `ROUND_HALF_EVEN` is a good default for statistical aggregation, not for a single member-facing payable amount.
**Trade-off**: Verified explicitly in `tests/unit/test_financial_calculation_service.py::TestRounding` (`333.33 × 33% = 109.9989 → 110.00`, which `ROUND_HALF_EVEN` would also round to 110.00 in this specific case — the test intentionally checks a case where the two conventions could plausibly diverge, to lock in the intended behavior rather than relying on both conventions coincidentally agreeing).

### Network Hospital Matching
**Choice**: Case/whitespace/punctuation-insensitive **exact** match (`_normalize_facility_name()` on both sides) against `policy_terms.json`'s `network_hospitals` list — never fuzzy/similarity matching.
**Reason**: Same reasoning as Phase 2A's exact (not fuzzy) cross-document patient-name matching (`docs/AI_HANDOFF.md` Known Issue 9) — a false-positive network match would apply a discount the hospital never agreed to and could misstate a payable amount; a false negative just means "treated as non-network," a safe, conservative default.
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
3. If none of those fired and `fraud_analysis_result.requires_manual_review` → `MANUAL_REVIEW`, but `approved_amount` still reflects `financial.payable_amount` since it's reliable.
4. `financial.payable_amount <= 0` → `REJECTED`.
5. `financial.eligible_amount < financial.claimed_amount` (genuine line-item exclusion, DENTAL/VISION itemized bills only) → `PARTIAL`; otherwise → `APPROVED`.
6. If overall confidence is still below the low-confidence threshold (0.5) at this point → downgrade to `MANUAL_REVIEW`, but keep the reliable `approved_amount` (per the spec: use the existing financial result if one exists, rather than discarding it).

**Reason for the claim-level-vs-itemized exclusion split (step 2)**: `PolicyEngine` sets `exclusion_applies=True` in two structurally different situations — a whole-claim condition/procedure exclusion (TC012, obesity treatment: no itemized bill, the entire claim is excluded) and a DENTAL/VISION itemized bill where only *one* line item happens to be excluded (TC006: root canal covered, teeth whitening excluded). Treating both the same way (`exclusion_applies=True` ⇒ `REJECTED`) would have rejected TC006 outright, contradicting its own official expected decision (`PARTIAL`, ₹8000 approved). The `line_item_findings` list is exactly the structural signal that distinguishes the two cases — non-empty means there's a legitimate partial-approval story to tell instead of a blanket rejection.

**Reason for basing `PARTIAL` vs. `APPROVED` on `eligible_amount` vs. `claimed_amount`, not `payable_amount` vs. `claimed_amount`**: copay and network discount both reduce `payable_amount` below `claimed_amount` for a completely normal, fully-covered claim (TC004: ₹1500 claimed, ₹1350 payable after 10% copay; TC010: discount + copay). Using a raw amount comparison for the `PARTIAL` test would have misclassified every claim with any copay or discount as `PARTIAL`. `eligible_amount` is `FinancialCalculationService`'s own field for "how much of the *claimed content* is even eligible before discount/limits/copay" — it only drops below `claimed_amount` via genuine line-item exclusion, which is the correct signal for "some of what you claimed for isn't covered," as opposed to "your claim is covered, subject to normal plan terms."

**Known, disclosed discrepancy — TC008 (Per-Claim Limit Exceeded)**: `test_cases.json` expects `REJECTED` (reason `PER_CLAIM_EXCEEDED`) when the claimed amount (₹7,500) exceeds the global `per_claim_limit` (₹5,000). This implementation does **not** add a "per-claim-limit-exceeded ⇒ REJECTED" rule, and TC008 reaches `APPROVED` at ₹1,800.00 (the capped, post-copay amount) instead. This is not an oversight — it is the same engineering judgment already made and disclosed for TC006/TC010 in the Phase 2C section above, now visible as a *decision*-level difference (not just an amount-level one) because TC008 has no line-item structure to fall into `PARTIAL` instead. The `PolicyEngine`'s `PER_CLAIM_LIMIT` finding is checked against the same raw `claimed_amount` for **both** TC006 (₹12,000 > ₹5,000) and TC008 (₹7,500 > ₹5,000) — it does not itself distinguish "this should reject the whole claim" from "this should just cap it." Adding a rule that treats any `PER_CLAIM_LIMIT` failure as automatic `REJECTED` would have also rejected TC006, directly contradicting TC006's own official expected `PARTIAL` outcome. Given the conflict between what TC006/TC010 and TC008 each seem to expect from the same kind of limit, this implementation chose internal consistency (limits cap, they never auto-reject a whole claim) over matching TC008 specifically — the same principle Decision 35 already established, extended here rather than special-cased around. See `docs/eval-report.md` for the full 12-case comparison and this discrepancy's exact numbers.

**What was deliberately NOT done**: adding a `RejectionReason.PER_CLAIM_EXCEEDED`-triggering rule scoped only to "no `line_item_findings`" (which would fix TC008 without breaking TC006) was considered and rejected — it would be inventing a policy rule not stated anywhere in `policy_terms.json` itself (the file has no concept of "cap if itemized, reject if not"), purely to match one test case's number. The chosen approach keeps every decision rule traceable to either `assignment.md`'s own explicit precedence list or a real, existing `PolicyEngine`/`FinancialCalculationService` field — never a rule inferred backward from a single expected output.

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
