# Manual UI Testing Guide

This directory contains **synthetic, fictitious** test documents (see the
footer on every PDF: *"SYNTHETIC TEST DOCUMENT — NOT A REAL MEDICAL
RECORD"*) for manually exercising the Plum Health Insurance Claims
Processing System end-to-end through its real UI. Nothing here modifies
the application — this is test data only.

See `TEST_MANIFEST.md` for the full per-file breakdown (purpose, expected
classification/quality, expected Phase 2A/2B/2C behavior, expected final
decision).

---

## 0. Prerequisites

1. Start the backend (`multi_agent_claims_pipeline/backend`):
   ```
   .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
   ```
2. Start the frontend (`multi_agent_claims_pipeline/frontend`):
   ```
   npm run dev
   ```
3. A **fresh or known-state** `data/claims.db` is strongly recommended
   before starting, especially before TC009/EXTRA05 (see §4 below) —
   those two rely on genuinely accumulating prior claims in the database
   during the test run, which will contaminate results if old claims
   from a previous session are still sitting in the same DB.

---

## 1. What the UI form does and doesn't expose

The claim submission form (`Claim Details` card) exposes: **Member ID**,
**Policy ID**, **Claim Category** (dropdown), **Treatment Date**,
**Claimed Amount (₹)**, and document file uploads. It does **not**
currently expose three fields that exist in the underlying API and matter
for a few specific test cases:

| Field | Exposed in UI? | Matters for | Workaround |
|---|---|---|---|
| `hospital_name` | No | TC010, EXTRA01 | **Not actually needed** — `PolicyEngine` resolves the hospital name from the uploaded HOSPITAL_BILL's *extracted* content (Phase 2B), not from a separate form field. As long as the bill document clearly states the hospital name (all TC010/EXTRA01 documents do), the network-hospital check works from the UI alone. |
| `ytd_claims_amount` | No (defaults to `0`) | TC004, TC005, TC008, TC010 (all specify a nonzero YTD in the spec) | **Doesn't change any outcome** — the annual OPD limit (₹50,000) is never the binding constraint in any of the 12 official cases at their specified YTD values either. Safe to ignore for UI testing; the field exists in the API for completeness/realism (`POST /api/v1/claims` accepts it as a multipart form field, `ytd_claims_amount`, if you want to test it directly with `curl`). |
| `simulate_component_failure` | No | **TC011** | **Required for TC011's core behavior** — the UI cannot trigger this. See §5 below for a `curl` command that sets it directly against the real API. |

`claims_history` (used by TC009/EXTRA05's fraud-pattern checks) is not a
form field at all — it's an evaluation-fixture-only mechanism. To see a
real same-day/monthly fraud signal through the UI, you build genuine
history by submitting prior claims first — see §4.

---

## 2. General per-case procedure

For every test case below:

1. Open the claim submission page.
2. Enter **Member ID** (see each case).
3. Enter **Policy ID**: always `PLUM_GHI_2024`.
4. Select **Claim Category** from the dropdown.
5. Enter **Treatment Date**.
6. Enter **Claimed Amount**.
7. Upload the PDF(s) listed for that case (from the case's subfolder).
8. Submit.
9. Open the claim detail page (should happen automatically on success).
10. Check the **Document Verification** section — does it match "Expected classification/quality" in `TEST_MANIFEST.md`?
11. Check the **Extracted Information** toggle on each document card.
12. Check the **Policy Evaluation** section (if reached).
13. Check the **Financial Calculation** section (if reached).
14. Check the **Fraud Analysis** section (if reached).
15. Check the **Trace** timeline — do the STARTED/COMPLETED/SKIPPED/FAILED events match expectations, including which stages are `SKIPPED` if the claim stopped early?
16. Record what actually happened.
17. Compare against `TEST_MANIFEST.md`'s "Expected ..." fields for that case.

---

## 3. Which cases stop early (and where)

| Case | Stops at | Why |
|---|---|---|
| **TC001** | `DOCUMENT_VERIFICATION` (BLOCKED) | Missing HOSPITAL_BILL |
| **TC002** | `DOCUMENT_VERIFICATION` (DOCUMENTS_PENDING — re-upload requested, not rejected) | Pharmacy bill UNREADABLE |
| **TC003** | `CROSS_DOCUMENT_VALIDATION` (BLOCKED) | Documents belong to different patients |
| **EXTRA07** | `CROSS_DOCUMENT_VALIDATION` (BLOCKED) | Both documents agree with each other but disagree with the claim's actual member (Phase 2A identity-fix regression) |
| **EXTRA08** | `CROSS_DOCUMENT_VALIDATION` (BLOCKED) | Documents disagree with *each other* (checked before member identity) |
| **EXTRA03** | Likely `CLAIM_VALIDATION` (before document verification even runs) | Claimed amount (₹400) below the ₹500 policy minimum |
| **TC013** *(not part of the official 12)* | `CROSS_DOCUMENT_VALIDATION` (BLOCKED) | No document shows a legible patient name at all — the no-legible-name gap fix |
| **TC014** *(not part of the official 12)* | `CROSS_DOCUMENT_VALIDATION` (BLOCKED) | One document is correctly named, the other shows no name at all — the every-document gap fix |
| All other cases (TC004-TC012 except TC003, EXTRA01/02/04/05/06/09, QUALITY_TESTS Q1-Q8) | Reach the end of the pipeline (`status=PROCESSING`) | No early-stop condition — Policy/Financial/Fraud all run; final decision (`APPROVED`/`PARTIAL`/`REJECTED`/`MANUAL_REVIEW`) is **not yet implemented** (Phase 2D), so `status` stays `PROCESSING` even for cases whose *official* expected outcome is REJECTED/PARTIAL/MANUAL_REVIEW — check the **Policy Evaluation** / **Financial Calculation** / **Fraud Analysis** sections directly for the underlying findings instead of a final verdict. |

For every case that reaches `POLICY_ENGINE`/`FINANCIAL_CALCULATION`/
`FRAUD_ANALYSIS`, those three trace events should show `COMPLETED` (or
`FAILED` only for TC011's deliberately-simulated failure — see §5).

---

## 4. TC009 / EXTRA05 — building real fraud history through the UI

`FraudAnalysisAgent` counts genuinely *persisted* prior claims for the
member (`ClaimRepository.list_by_member`) when no fixture history is
supplied — which is always true for real UI submissions. To reproduce the
official same-day/monthly fraud signals **for real**, submit claims in
this order:

### TC009 (same-day, `same_day_claims_limit = 2`)
1. Submit 3 small CONSULTATION claims for **EMP008**, treatment date
   **2024-10-30** (any modest amount/category is fine — you can reuse
   `TC009_same_day_fraud/F017_prescription_ravi.pdf` +
   `F018_hospital_bill_ravi.pdf` three times, or any other EMP008
   documents you have, changing nothing but resubmitting).
2. Then submit the real TC009 case (`F017`/`F018`, ₹4800) as the 4th
   same-day claim.
3. Check **Fraud Analysis**: `same_day_claim_count` should be `4`,
   `SAME_DAY_CLAIMS_LIMIT_EXCEEDED` triggered, `risk_level=HIGH`,
   `requires_manual_review=True`.

### EXTRA05 (monthly, `monthly_claims_limit = 6`)
1. Submit 6 CONSULTATION claims for **EMP008**, spread across different
   October 2024 dates (avoid `2024-10-26` and avoid duplicating a single
   date more than `same_day_claims_limit` times, so you don't
   accidentally also trip the same-day check).
2. Then submit the real EXTRA05 case
   (`EXTRA05_prescription_monthly.pdf` / `EXTRA05_hospital_bill_monthly.pdf`,
   dated 2024-10-26) as the 7th claim that month.
3. Check **Fraud Analysis**: `monthly_claim_count` should be `7`,
   `MONTHLY_CLAIMS_LIMIT_EXCEEDED` triggered.

If you don't want to seed real history, you can still confirm the
*deterministic-threshold logic itself* is correct via the automated test
suite (`tests/unit/test_fraud_analysis_agent.py`), which exercises exactly
these thresholds using injected fixture history — the manual UI walk above
is for confirming the *real, persisted* code path specifically.

---

## 5. TC011 — simulating a component failure via the API directly

The UI has no checkbox for `simulate_component_failure`. To see the real
degrade-gracefully behavior, submit TC011 with `curl` instead (adjust the
file paths if your shell's current directory differs):

```bash
curl -X POST http://localhost:8000/api/v1/claims \
  -F "member_id=EMP006" \
  -F "policy_id=PLUM_GHI_2024" \
  -F "claim_category=ALTERNATIVE_MEDICINE" \
  -F "treatment_date=2024-10-28" \
  -F "claimed_amount=4000" \
  -F "simulate_component_failure=true" \
  -F "documents=@TC011_component_failure/F021_ayurvedic_prescription.pdf;type=application/pdf" \
  -F "documents=@TC011_component_failure/F022_ayurvedic_hospital_bill.pdf;type=application/pdf"
```

Then open the returned `claim_id` in the UI's claim detail page as usual.
Expected: the pipeline does **not** crash, `FRAUD_ANALYSIS` shows `FAILED`
in the trace (not `COMPLETED`), `fraud_analysis_result` is `null`, and the
claim still reaches the end of the pipeline with `status=PROCESSING` —
everything else (Policy/Financial) should have run normally, since the
simulated failure only affects `FraudAnalysisAgent`.

You can still submit TC011 through the plain UI (without the flag) to
confirm the "happy path" — Policy/Financial/Fraud all `COMPLETED` — as a
baseline comparison.

---

## 6. Directory reference

```
test_documents/
├── TC001_wrong_document/           F001, F002
├── TC002_unreadable_document/      F003, F004
├── TC003_different_patients/       F005, F006
├── TC004_clean_consultation/       F007, F008
├── TC005_diabetes_waiting_period/  F009, F010
├── TC006_dental_partial/           F011
├── TC007_mri_pre_auth/             F012, F013, F014
├── TC008_per_claim_limit/          F015, F016
├── TC009_same_day_fraud/           F017, F018  (see §4 for history seeding)
├── TC010_network_hospital/         F019, F020
├── TC011_component_failure/        F021, F022  (see §5 for the failure flag)
├── TC012_excluded_treatment/       F023, F024
├── TC013_no_legible_patient_name/  F025, F026  (not part of the official 12 — see below)
├── TC014_partial_identification/   F027, F028  (not part of the official 12 — see below)
├── EXTRA_PHASE2C/                  EXTRA01-EXTRA09 (18 files — see TEST_MANIFEST.md)
├── QUALITY_TESTS/                  Q1-Q8 (document-robustness variations)
├── TEST_MANIFEST.md                Full per-file expected-behavior breakdown
├── generate_test_documents.py      The generator script (see §7 to regenerate)
└── lib_docbuilder.py               Shared ReportLab/Pillow document-building helpers
```

---

## 7. Regenerating these documents

This tooling is intentionally isolated from the application's own
`backend/.venv` — it needs `reportlab`, `Pillow`, and `numpy`, none of
which the claims-processing backend itself depends on. From a fresh venv:

```bash
python -m venv .docgen_venv
.docgen_venv\Scripts\python.exe -m pip install reportlab pillow numpy
.docgen_venv\Scripts\python.exe test_documents\generate_test_documents.py
```

This regenerates every PDF plus `TEST_MANIFEST.md` (derived from the same
in-script data, so it can never drift out of sync with the actual files).
`README.md` (this file) is maintained by hand, not regenerated.

Multilingual rendering (`multilingual_prescription.pdf`) uses Windows'
bundled `Nirmala.ttc` (Devanagari) font at
`C:/Windows/Fonts/Nirmala.ttc` — on a non-Windows machine it falls back to
the regular Latin font (Hindi text will render as tofu boxes); regenerate
on Windows for correct output.
