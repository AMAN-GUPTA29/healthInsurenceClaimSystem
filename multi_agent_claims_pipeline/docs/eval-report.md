# Evaluation Report

All 12 official test cases from `test_cases.json`, run through the real, complete `ClaimsPipeline` (all 9 stages: Claim Validation → Document Verification → Cross-Document Validation → Document Extraction → Policy Evaluation → Financial Calculation → Fraud Analysis → Decision Generation → Explanation).

## Methodology

- **Classification**: `DocumentInputAdapter.to_domain()` — the same fixture ground-truth path (`actual_type`/`quality`/`patient_name_on_doc`) the evaluation runner uses for TC001-TC003.
- **Extraction**: built directly from each case's own `content` block in `test_cases.json` — standing in for what real Gemini extraction would produce on these clean, unambiguous documents. Real Gemini extraction on the actual assignment fixture PDFs was already verified live in Phase 2B (see `docs/AI_HANDOFF.md` "Real AI Verification (Phase 2B)"); Policy/Financial/Fraud/Decision Generation make no AI calls at all, so this substitution affects nothing about their correctness.
- **Explanation**: `ExplanationAgent` was run with **no AI provider configured**, so every case below shows `explanation_source=FALLBACK` (the deterministic explanation path, not the LLM). A corporate SSL-inspection proxy in this environment blocks outbound HTTPS to Google's API (root-caused to a misconfigured corporate root CA — see `docs/AI_HANDOFF.md` Known Issues); disabling TLS verification to work around it was deliberately not attempted. The real AI explanation path was unit-tested with a fake provider (`tests/unit/test_explanation_agent.py`) and is otherwise identical in every other respect — this substitution affects only the *prose*, never the decision, amount, or any deterministic finding below.
- `test_cases.json`/`policy_terms.json` were never modified.

## Results

| TC | Case | Expected Decision | Actual Decision | Expected Amount | Actual Amount | Trace | Pass/Fail |
|----|------|-------------------|------------------|------------------|----------------|-------|-----------|
| TC001 | Wrong Document Uploaded | *(none — stop early)* | *(none)* — `BLOCKED` at `DOCUMENT_VERIFICATION` | — | — | 0 FAILED events | **PASS** |
| TC002 | Unreadable Document | *(none — stop early)* | *(none)* — `DOCUMENTS_PENDING` at `DOCUMENT_VERIFICATION` | — | — | 0 FAILED events | **PASS** |
| TC003 | Documents Belong to Different Patients | *(none — stop early)* | *(none)* — `BLOCKED` at `CROSS_DOCUMENT_VALIDATION` | — | — | 0 FAILED events | **PASS** |
| TC004 | Clean Consultation — Full Approval | `APPROVED` | `APPROVED` | ₹1350 | ₹1350.00 | 0 FAILED events | **PASS** |
| TC005 | Waiting Period — Diabetes | `REJECTED` (`WAITING_PERIOD`) | `REJECTED` (`WAITING_PERIOD`) | — | ₹0 | 0 FAILED events | **PASS** |
| TC006 | Dental Partial — Cosmetic Exclusion | `PARTIAL` | `PARTIAL` | ₹8000 | **₹5000.00** | 0 FAILED events | **PASS (decision) / DIFFERS (amount)** — see below |
| TC007 | MRI Without Pre-Authorization | `REJECTED` (`PRE_AUTH_MISSING`) | `REJECTED` (`PRE_AUTH_MISSING`) | — | ₹0 | 0 FAILED events | **PASS** |
| TC008 | Per-Claim Limit Exceeded | `REJECTED` (`PER_CLAIM_EXCEEDED`) | **`APPROVED`** | — | ₹1800.00 | 0 FAILED events | **FAIL (decision)** — see below |
| TC009 | Fraud Signal — Multiple Same-Day Claims | `MANUAL_REVIEW` | `MANUAL_REVIEW` (`MANUAL_REVIEW_FRAUD`) | — | ₹1800.00 | 0 FAILED events | **PASS** |
| TC010 | Network Hospital — Discount Applied | `APPROVED` | `APPROVED` | ₹3240 | **₹1800.00** | 0 FAILED events | **PASS (decision) / DIFFERS (amount)** — see below |
| TC011 | Component Failure — Graceful Degradation | `APPROVED`, resilient | `APPROVED`, confidence 0.85 (vs. 1.0 clean) | — | ₹4000.00 | **1 FAILED event (FRAUD_ANALYSIS, simulated)** | **PASS** |
| TC012 | Excluded Treatment | `REJECTED` (`EXCLUDED_CONDITION`) | `REJECTED` (`EXCLUDED_CONDITION`, `WAITING_PERIOD`) | — | ₹0 | 0 FAILED events | **PASS** |

**11 / 12 decisions match exactly.** 2 of those 11 (TC006, TC010) match on decision but differ on the computed amount — a disclosed, deliberate trade-off, not a bug (see below). 1 case (TC008) differs on decision itself.

## Where actual differs from expected, and why

### TC006 / TC010 — amount differs (Financial Calculation Order, Phase 2C Decision 35)

`FinancialCalculationService` applies the category `sub_limit` and the global `per_claim_limit` as **real caps** in the calculation chain, per the assignment's own literal rule list (`policy_terms.json`'s limits section). `test_cases.json`'s own worked examples for TC006 and TC010 do not appear to apply those same caps:

- **TC006**: the non-excluded line item (Root Canal, ₹8000) is below the DENTAL category's own sub-limit (₹10,000), but the *global* `per_claim_limit` (₹5,000) still applies — capping the payable amount to ₹5,000.00, not the assignment's expected ₹8,000.
- **TC010**: the network-discounted amount (₹4,500 → ₹3,600) exceeds the CONSULTATION category's sub-limit (₹2,000) — capping the payable amount to ₹1,800.00 (after copay) rather than the assignment's expected ₹3,240 (which applies the network discount and copay but not the sub-limit).

This was a deliberate choice made in Phase 2C (see `docs/tradeoffs.md` "Financial Calculation Order"), not something Phase 2D introduced or could silently "fix" without un-implementing a real policy rule that two of the twelve cases' own reference numbers appear to have omitted. The **decision** itself (PARTIAL / APPROVED) still matches the assignment's expectation in both cases — only the payable figure differs.

### TC008 — decision differs (APPROVED vs. expected REJECTED)

`test_cases.json` expects the whole claim to be `REJECTED` with reason `PER_CLAIM_EXCEEDED` when the claimed amount (₹7,500) exceeds the global per-claim limit (₹5,000). This implementation's `DecisionGenerationAgent` does not treat "per-claim limit exceeded" as an automatic full rejection — it treats it exactly like TC006's and TC010's limit/discount/copay math: a deterministic cap that reduces the payable amount, not a reason to reject the whole claim. Since the claim in TC008 has no line-item structure to fall back on, it reaches `APPROVED` at the capped amount (₹1,800.00 after copay) rather than `PARTIAL` or `REJECTED`.

**Why this reading was chosen over adding a "per-claim-limit-exceeded ⇒ REJECTED" rule**: the assignment's own worked examples for TC006 and TC010 already establish that exceeding a limit (sub-limit, in those two cases) does **not** cause a full rejection — it reduces the payable amount and the claim is still `PARTIAL`/`APPROVED`. `policy_terms.json`'s `PER_CLAIM_LIMIT` finding is checked against the same raw `claimed_amount` for both TC006 (₹12,000 > ₹5,000) and TC008 (₹7,500 > ₹5,000) — the deterministic `PolicyEngine` finding does not itself distinguish "this is the kind of limit-exceeded that should reject the whole claim" from "this is the kind that should just cap it." Adding a rule that treats a `PER_CLAIM_LIMIT` failure as an automatic `REJECTED` would have made TC006 *also* reject (it exceeds the same global per-claim limit), directly contradicting TC006's own expected `PARTIAL` outcome. Given the conflict between the two officially-expected outcomes, this implementation is internally consistent (limits cap, they never auto-reject) rather than special-cased per test case — the exact same engineering judgment already applied and disclosed for TC006/TC010 in Phase 2C, now visible here as a decision-level (not just amount-level) difference because TC008 has no line-item structure to produce a `PARTIAL` outcome instead. See `docs/tradeoffs.md` "Decision Precedence" for the full reasoning. This was not silently special-cased to force a match — it is disclosed here and in the code's own docstrings/comments.

## TC011 — component-failure resilience, confirmed

`simulate_component_failure=true` genuinely raised inside `FraudAnalysisAgent` during this run (visible as the one real `FAILED` trace event above) — the pipeline did not crash, did not silently continue as if fraud analysis had passed, and reached a final decision (`APPROVED`) with `confidence_score=0.85` (vs. `1.0` for an equivalent clean run) and `manual_review_recommended=true`, satisfying the assignment's explicit resilience requirement for this case.

## Confidence and explanation notes

Every case's `confidence_score` reflects the deterministic strategy in `docs/tradeoffs.md` "Decision Confidence Strategy" — the minimum confidence across every stage that ran, minus a penalty per degraded/missing component. Cases with a `NETWORK_HOSPITAL: WARNING` finding (hospital name not resolvable to a definite network/non-network match) show `confidence=0.6`, matching `PolicyEngine`'s own confidence cap for that finding (Decision — see `app/policy/policy_engine.py::_check_network`).

Every case's explanation is the deterministic **fallback** (see Methodology above) — built directly from the same `ClaimDecision` fields the LLM path would also have received, never a placeholder.

---
*Regenerate this table with `run_all_12_eval.py` (not part of the committed test suite — a standalone verification script; see `docs/AI_HANDOFF.md` "Verification (Phase 2D)" for how to reproduce this run).*
