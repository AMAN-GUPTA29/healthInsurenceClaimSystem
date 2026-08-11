# Architecture — Health Insurance Claims Processing System

## Overview

This document describes the architectural decisions made for the claims processing system. It will be updated at the end of each phase.

---

## Core Principles

### 1. Modular AI Layer

The most critical architectural constraint: **no business agent may directly depend on an AI vendor SDK**.

```
Business Agent
    │
    ▼
AIProvider (ABC)             ← app/ai/providers/base.py
    │
    ├── AnthropicProvider    ← app/ai/providers/anthropic_provider.py
    │   └── import anthropic  ← ONLY FILE with SDK import
    │
    ├── GeminiProvider       ← future
    └── OpenAIProvider       ← future
```

This design means:
- Switching from Claude to GPT-4 or Gemini requires ONE file change (adding a new provider)
- Business logic in agents is completely re-usable
- Tests can inject a test double without mocking vendor SDKs

### 2. Dependency Injection

The AI provider is constructed once at startup and injected:

```
Settings → create_ai_provider() → AIProvider instance
                                       │
                                 FastAPI DI (Depends)
                                       │
                               Route handlers → Agents
```

No global clients. No module-level `client = Anthropic()` calls.

### 3. Deterministic Policy Evaluation

The LLM assists with classification and extraction. It is **never** the final authority on:
- Whether a claim is covered
- Financial calculations (copay, network discount, limits)
- Waiting period arithmetic
- Exclusion matching

These are evaluated deterministically by the PolicyEngine from `policy_terms.json`.

### 4. Structured Schemas Between Components

Every component boundary uses typed Pydantic models:
- `ClaimSubmission` → `Claim` → `ClaimDecision`
- `AIGenerateRequest` / `AIStructuredRequest` / `DocumentAnalysisRequest`
- `AIGenerateResponse` / `AIStructuredResponse` / `DocumentAnalysisResponse`

No component passes raw strings or dicts across its boundary.

### 5. Graceful Degradation

Every error in the system has a `recoverable` flag. The orchestrator uses this to decide:

| Error Type | Recoverable | Pipeline Action |
|------------|-------------|-----------------|
| Document unreadable | ✅ | Ask member to resubmit |
| Extraction partial | ✅ | Lower confidence, continue |
| AI timeout | ✅ | Skip agent, note failure |
| Auth failure | ❌ | Stop pipeline |
| Policy not found | ❌ | Stop pipeline |

### 6. Observability Is First-Class, Not an Afterthought

The assignment weighs observability at 20% of evaluation — on par with
engineering quality — because a claims decision that can't be explained is
not acceptable in an insurance context, independent of whether it happens
to be correct. So Phase 1 builds the trace system *before* any actual
claims logic exists, rather than bolting logging onto agents after the
fact. Every future agent (`ClaimValidationAgent`, `PolicyEngine`,
`FraudAnalysisAgent`, ...) is built against this infrastructure from day
one instead of retrofitting explainability later.

**How trace context flows through the system**: a `TraceContext`
(`trace_id` + `claim_id`) is constructed once per pipeline run and passed
by constructor injection into a `TraceService`. Components never construct
their own trace IDs, never reach into global state, and never invent
free-form status strings — they call `tracer.span(TraceComponent.X)` or the
explicit `started`/`completed`/`failed`/`warning`/`skipped` methods against
the one `TraceService` instance the pipeline orchestrator hands them.

```
Pipeline orchestrator (Phase 2)
    │
    ├── TraceContext.new(claim_id)
    │
    ▼
TraceService(context, sink=TraceRepository())
    │
    ├── injected into ── ClaimValidationAgent
    ├── injected into ── DocumentVerificationAgent
    ├── injected into ── PolicyEngine
    └── injected into ── ... every future component
```

**How trace events are persisted**: `TraceService` is DB-agnostic — it
accepts any `sink` satisfying `async def record(event)`. In production
that's `TraceRepository`, which persists each event immediately (awaited,
not batched) to the `trace_events` table via the existing async SQLAlchemy
foundation. In unit tests, `TraceService` is used with no sink at all —
events still accumulate in `.events` — so pipeline component tests never
need a real database.

**Why raw prompts/documents are not stored in the trace**: `TraceEvent.metadata`
is designed around small, structured, summarized facts —
`{"document_type": "PRESCRIPTION", "quality": "GOOD"}`, not the document
image or the full LLM prompt/response. Three reasons: (1) trace rows are
meant to be cheap to list and render in a UI — a 2MB base64 image or a
10KB prompt per event would make that unusable; (2) full documents already
have their own storage lifecycle (uploaded files), duplicating them into
every trace event bloats the database for no benefit; (3) it keeps the
redaction surface small — `redact_metadata` only has to reason about a
handful of structured keys, not scan arbitrary prompt text for leaked
secrets. If a future phase needs the full extracted payload for debugging,
it belongs in a separate `extractions` table referenced by `file_id`, not
inlined into every trace event.

**How the trace supports explainability**: `GET /api/v1/claims/{claim_id}/trace`
returns every event for a claim in chronological order (ordered by DB
insertion sequence, not wall-clock timestamp, since two events can share a
millisecond). For any decision, an operations user can see exactly which
components ran, in what order, what each one attempted (`message`,
`metadata`), whether it succeeded/failed/warned/was skipped, how confident
it was, how long it took, and — on failure — the exact (safe) error type,
code, and whether the pipeline was able to continue. This is the mechanism
the assignment's "reconstruct exactly why any claim got any decision just
from the trace" requirement is built against.

---

## Claim Processing Flow (Phase 2A)

Phase 2A implements the first three real pipeline stages — claim
validation, document verification, cross-document validation — and
deliberately stops there. No policy evaluation, financial calculation, or
decision (APPROVED/PARTIAL/REJECTED/MANUAL_REVIEW) exists yet.

```
POST /api/v1/claims  (multipart/form-data: fields + real files)
        │
        ▼
validate_upload() per file   (magic bytes, size, extension — never trust Content-Type)
        │
        ▼
DocumentStorage.save() per file   → storage_reference (UUID filename, never client's)
        │
        ▼
DocumentInputAdapter.from_uploads(fields, uploads, document_storage)
        │  → (ClaimSubmission, {})   ← empty: no classification pre-supplied for real uploads
        ▼
ClaimsPipeline.run(claim, classifications, tracer)
        │
        ├─▶ ClaimValidationAgent            (member, policy, category, amount)
        │       │ invalid → stop, status=BLOCKED
        │       ▼
        ├─▶ DocumentVerificationAgent       (required docs from PolicyRepository;
        │       │                            reads real bytes via DocumentStorage.read()
        │       │                            and calls AIProvider.analyze_document() —
        │       │                            classification from actual content, never
        │       │                            filename or user selection)
        │       │ NEEDS_RESUBMISSION → stop, status=DOCUMENTS_PENDING
        │       │ BLOCKED            → stop, status=BLOCKED
        │       ▼
        └─▶ CrossDocumentValidationAgent    (patient-identity match across
                │                            already-extracted names)
                │ BLOCKED → stop, status=BLOCKED
                ▼
        status=PROCESSING ("Phase 2A cleared, policy evaluation not yet implemented")
```

The evaluation runner takes a parallel path through the same pipeline:
`DocumentInputAdapter.to_domain(test_case)` → pre-supplied classifications
(ground truth from `test_cases.json`, no `DocumentStorage`/AI call involved)
→ the identical `ClaimsPipeline.run(...)`. Both paths converge immediately
after the input boundary; no agent or pipeline code knows or cares which
one produced its input. See "Real vs. fixture input" below.

### Claim validation
`ClaimValidationAgent` is purely deterministic (`ai_provider=None`) — member
existence and policy ID come from `PolicyRepository` (backed by
`policy_terms.json`, never hardcoded); category and minimum-amount checks
follow the same rule. It never raises for an expected failure; it always
returns a `ValidationResult(valid, errors, warnings)` and the pipeline
decides whether that's a stop.

### Document verification
`DocumentVerificationAgent` is the one place a real AI call happens in
Phase 2A. For each document, it uses a pre-supplied `DocumentClassification`
if one exists (see "Real vs. fixture input" below); otherwise it branches on
whether the document has a `storage_reference`. A real upload always does —
`_classify_from_content()` reads the actual bytes via `DocumentStorage.read()`
and calls the injected `AIProvider.analyze_document()`, Gemini's multimodal
path, so the type/quality/patient-name/confidence come from what the model
actually sees in the image or PDF, never from the filename or any
user-declared type (there is no such field in the API anymore). A fixture
with no ground truth (rare) falls back to `_classify_from_text_only()` via
`AIProvider.generate_structured()`. Required/optional document types per
category come from `PolicyRepository.get_document_requirements()`, never a
Python `if category == "CONSULTATION"` branch. The `user_message` returned
to the member is generated from the structured result (counts of what was
uploaded, what's missing, what's unreadable) — the same code produces
TC001's and TC002's very different messages; nothing is hardcoded per test
case. Verified against real uploaded synthetic documents processed by the
real Gemini API — see `docs/AI_HANDOFF.md` "Real Document Upload
Verification."

### Document storage
`DocumentStorage` (an ABC in `app/storage/document_storage.py`) is injected
into the API layer the same way `AIProvider` and `TraceService` are — no
agent imports it directly except `DocumentVerificationAgent`, and only to
call `read()`. `LocalFileDocumentStorage` writes validated bytes to
`{upload_dir}/{claim_id}/{uuid}.{ext}`; the UUID filename (never the
client's original filename) closes path-traversal and collision risk at
the point of storage, not as an afterthought. `storage_reference` — the
actual disk path — is on the domain model and ORM row but is deliberately
excluded from every API response schema; a client can see a document's
original filename, AI-determined type/quality/patient/confidence, and
processing status, never where it physically lives on disk.

### Cross-document validation
`CrossDocumentValidationAgent` is also purely deterministic — it compares
patient names `DocumentVerificationAgent` already extracted (or the
fixture already supplied). It never re-reads a document and never calls
AI itself; if the classification step got the name wrong, that's a
document-verification-quality problem, not a cross-document one.

### Real vs. fixture input — one boundary, not two systems
`DocumentInputAdapter` exposes two entry points — `to_domain()` for
evaluation fixtures (ground truth from `test_cases.json`: `actual_type`/
`quality`/`patient_name_on_doc`) and `from_uploads()` for real API
submissions (actual `UploadFile`s, validated and persisted via
`DocumentStorage` before the adapter ever sees them). Both produce the
exact same `(ClaimSubmission, Dict[str, DocumentClassification])` shape
that `ClaimsPipeline` consumes — for a real upload the classifications map
is always empty, forcing `DocumentVerificationAgent` down its real
multimodal path; for a fixture it's pre-populated, skipping the AI call
entirely. No agent branches on "is this a test case" or "is this a real
upload" — the data shape alone determines behavior. This is deliberately
the same pattern as the AI provider abstraction: swap what's behind the
boundary, not the code that consumes it.

### Early stopping, in the trace
See "Observability" above for the full event-type rationale. Concretely,
for TC001 (missing document): `CLAIM_VALIDATION` STARTED→COMPLETED,
`DOCUMENT_VERIFICATION` STARTED→COMPLETED (metadata `status: BLOCKED`),
`CROSS_DOCUMENT_VALIDATION` SKIPPED, `PIPELINE` WARNING. No
`DECISION_GENERATION` event is ever emitted — Phase 2A doesn't reach it,
and the trace shows that honestly by omission rather than a fabricated
"NOT_RUN" event.

### Failure handling (AI/infra failures vs. expected business outcomes)
A document verification agent that correctly finds a missing document has
not failed — it did its job. `FAILED` trace events and raised exceptions
are reserved for genuine problems: AI timeout, rate limit, or an
unparseable structured response (`ExtractionError`). `ClaimsPipeline`
catches these per-stage, records `FAILED` (with safe, structured error
info — never a stack trace or the API key), marks downstream stages
`SKIPPED`, and returns a degraded but valid `Claim`
(`status=BLOCKED`, an explanatory `user_message`) instead of raising or
crashing the request. Verified live against the real Gemini API with an
invalid key (see `docs/AI_HANDOFF.md`): the request reached Google's
servers, was rejected for auth, and the whole chain — trace, persisted
claim, HTTP response — degraded exactly as designed.

---

## Document Extraction (Phase 2B)

Phase 2B adds a fourth pipeline stage, `DocumentExtractionAgent`, running
after cross-document validation:

```
Claim Validation → Document Verification → Cross-Document Validation
                                                       │
                                                       ▼ (only if PASS)
                                          DocumentExtractionAgent
                                          (one AIProvider.analyze_document()
                                           call per extractable document)
                                                       │
                                                       ▼
                                          status=PROCESSING, extraction_result set
```

### Why verification and extraction are separate agents
`DocumentVerificationAgent` answers "is this the correct document and is it
usable?" — a cheap-ish classification (type/quality/patient-name/
confidence) whose job is to fail fast and specifically when the member
uploaded the wrong thing (TC001) or something unreadable (TC002). Merging
full structured extraction into that agent would mean every claim pays
extraction's cost (slower, more tokens, six document-specific schemas)
*before* knowing whether the claim will even survive verification — most
of that work would be wasted on a claim that was always going to stop at
"wrong document type." Keeping them separate means `DocumentExtractionAgent`
only ever runs on documents that already passed the cheap check, and
each agent's prompt/schema can evolve independently without touching the
other's contract. `docs/component-contracts.md` documents both fully.

### Why extraction is document-type-specific, not one generic schema
A hospital bill's meaningful fields (line items, subtotal, discount, tax)
share almost nothing with a lab report's (test name, result, unit,
reference range, abnormal flag). A single generic "extract everything"
schema would either be a superset with mostly-empty fields on every
document (noisy, harder to validate, harder for the AI to fill correctly)
or a lossy lowest-common-denominator. Six typed Pydantic schemas
(`app/domain/extraction.py`) — one per supported `DocumentType` — each ask
the AI for exactly the fields that document type can actually contain,
matching `sample_documents_guide.md`'s own per-type field lists. A shared
`ExtractionBase` (`confidence`/`warnings`/`evidence`) avoids duplicating
the cross-cutting fields, and a discriminated union
(`Field(discriminator="document_type")`) lets `DocumentExtractionResult`
carry any of the six without a manual type-check at every read site.

### How documents reach the AI, and why the schema avoids `null`
Same real-content path `DocumentVerificationAgent` already established
(Decision 22/24): `DocumentExtractionAgent` reads the actual bytes via
`DocumentStorage.read(doc.storage_reference)` and sends them through
`AIProvider.analyze_document()` — never re-derives anything from a
filename. The AI-facing JSON schemas
(`app/ai/prompts/*_extraction.py`) deliberately never use `null`: Gemini's
`response_schema` is an OpenAPI-3.0-like subset that doesn't reliably
support nullable unions (Decision 8), so "not visible" is represented with
an explicit sentinel — `""` for strings/dates/amounts, `[]` for lists,
`"UNCLEAR"` for the tri-state signature/stamp/abnormal-flag fields — and
`app/domain/extraction.py`'s validators translate those sentinels into
real `None`/`Decimal`/`bool` on the way into the domain model. This is the
same pattern `document_verification.py`'s classification schema already
uses (`patient_name: ""` instead of `null`), generalised across six more
schemas rather than reinvented per schema.

### How structured output is validated
`AIProvider.analyze_document()` returns a raw dict
(`DocumentAnalysisResponse.structured_data`). `DocumentExtractionAgent`
never lets that dict flow further unvalidated — it's immediately passed
to a per-document-type adapter function (`_adapt_prescription`,
`_adapt_hospital_bill`, ...) that reshapes the AI's flat field names into
the domain model's nested shape (e.g. `doctor_name`/`doctor_registration_number`
→ a `DoctorInfo` sub-object) and constructs the typed Pydantic model. A
`KeyError` (AI omitted a required field) or `ValidationError` (AI returned
something the schema rejects — e.g. an unparseable enum) is caught and
raised as `ExtractionError`, which the caller turns into a
`DocumentExtractionFailure` rather than persisting a malformed/partial
dict.

### How failures are handled — per document, not per claim
Real AI calls fail sometimes (this was verified *live*, not just in
tests — see `docs/AI_HANDOFF.md` "Real AI Verification (Phase 2B)": a
genuine Gemini rate-limit error occurred organically mid-verification).
`DocumentExtractionAgent.run()` wraps each document's extraction in its
own `try/except Exception`, so one failure never stops the others — the
loop continues, the failure is recorded in `ClaimExtractionResult.failures`,
and `has_failures=True` degrades the claim's `user_message` without
blocking it (`status` stays `PROCESSING`). This mirrors
`ClaimsPipeline.run()`'s own claim-level never-raise guarantee (Decision
18) at one level down — a document-level version of the same idea, for
the same reason: a system that can't tolerate one bad input is not
production-quality for an insurer processing tens of thousands of real,
messy documents.

### How extraction is persisted
Hybrid, not an opaque blob (see `docs/component-contracts.md`
"ClaimRepository — Extraction persistence" for the full column list): the
complete typed envelope is stored as JSON on `ClaimDocumentORM.extraction_json`
(the source of truth, rehydrated via the same discriminated union that
validated it going in), while a handful of fields genuinely likely to be
queried later (`diagnosis`, `treatment`, `document_date`, `doctor_name`,
`total_amount`) are denormalised onto real columns at save time. Claim-level
rollup (failures/skipped/ai_calls/confidence) lives on
`ClaimORM.extraction_summary_json`, matching the existing `*_result_json`
pattern for validation/verification/cross-document results.

### How the frontend displays extraction
`ClaimDetail.tsx`'s `DocumentCard` gains a collapsed-by-default "Extracted
Information" section per document — never raw JSON as the primary view.
`ExtractedInfo` switches on `extraction.extraction.document_type` and
renders a document-type-specific layout (doctor/diagnosis/medications for
a prescription; line items/total for a bill; test results for a lab
report; ...), plus any `warnings` the AI raised. A document that failed
extraction shows the specific failure reason (from
`extraction_result.failures`) instead of a toggle with nothing behind it.

### How explainability is preserved
Every extraction attempt is traced the same way every other stage is
(`docs/architecture.md` §6's rules apply unchanged): one `DOCUMENT_EXTRACTION`
`STARTED`→`COMPLETED` pair per claim, `ai_metadata` capturing
provider/model/latency, and `metadata` summarising counts
(`documents_extracted`/`failures`/`skipped`/`has_failures`) — never the
full extracted payload or a raw AI response in the trace itself (that
belongs in `extraction_json`, which already has its own storage lifecycle,
per the same reasoning §6 gives for not duplicating documents into trace
rows). An operations user can see from the trace alone whether extraction
ran, how long it took, which provider/model answered, and how many
documents succeeded vs. failed — without opening the full claim record.

---

## Policy Evaluation, Financial Calculation & Fraud Analysis (Phase 2C)

Phase 2C adds three more pipeline stages after document extraction —
`PolicyEngine`, `FinancialCalculationService`, `FraudAnalysisAgent` — none
of which make any AI calls:

```
Claim Validation → Document Verification → Cross-Document Validation → Document Extraction
                                                                                  │
                                                                                  ▼ (regardless of extraction outcome)
                                                                        PolicyEngine (deterministic)
                                                                                  │
                                                                    ┌─────────────┴─────────────┐
                                                                    ▼ (only if policy succeeded)  ▼ (always attempted)
                                                        FinancialCalculationService      FraudAnalysisAgent
                                                                    │                             │
                                                                    └─────────────┬───────────────┘
                                                                                  ▼
                                                          status stays PROCESSING — no final decision yet
```

### Why these three are deterministic, never AI-driven
Core Principle 3 (above) already commits to this: the LLM is never the
final authority on coverage, financial arithmetic, or waiting-period math.
Phase 2C is where that principle actually gets implemented — `PolicyEngine`
reads only `policy_terms.json` (via `PolicyRepository`) and the claim's
already-extracted structured data (Phase 2B); it makes zero AI calls, the
same `ai_provider=None` pattern `ClaimValidationAgent` established in
Phase 2A. `FinancialCalculationService` is pure `Decimal` arithmetic over
`PolicyEngine`'s output — no AI, no policy-rule knowledge of its own.
`FraudAnalysisAgent` reads numeric thresholds from `policy_terms.json`'s
`fraud_thresholds` and historical claim counts — also zero AI calls in
this phase. An AI-extracted bill total or diagnosis string is *evidence*
these deterministic components read, never something they defer a
financial or coverage decision to.

### Why Policy → Financial → Fraud, and why Fraud runs independently
Financial calculation genuinely depends on policy evaluation's output
(sub-limit, copay%, network discount%, whether coverage even applies) —
there is no meaningful "payable amount" without first knowing what the
policy says, so `FinancialCalculationService` is skipped whenever
`PolicyEngine` failed or produced nothing. Fraud analysis has no such
dependency: same-day claim patterns, monthly claim counts, and
high-value/auto-manual-review thresholds are meaningful signals regardless
of whether coverage or a payable amount could be computed for this
specific claim — a claim that fails policy evaluation for an unrelated
reason (e.g. a missing extraction field) should still get a fraud read if
one is possible. `ClaimsPipeline` reflects this directly: fraud analysis
is attempted unconditionally (skipped only if the agent itself isn't
configured), while financial calculation's skip condition explicitly
checks `claim.policy_evaluation_result is not None` first.

### Soft-fail, not hard-stop — a deliberate departure from Phase 2A/2B
Every Phase 2A stage (claim validation, document verification,
cross-document validation) can *early-stop* the claim — `status=BLOCKED`,
downstream stages skipped, because those are prerequisites: there is no
meaningful policy evaluation for a claim whose documents don't even belong
to the claimed member. Phase 2C's three stages are different: the
assignment explicitly scopes final decision generation
(`APPROVED`/`PARTIAL`/`REJECTED`/`MANUAL_REVIEW`) out of this phase, so
none of Policy/Financial/Fraud may set a terminal status — they *inform* a
decision that doesn't exist yet. `ClaimsPipeline._run_soft_stage()` (a new
sibling to the existing `_run_stage()`) catches any exception from these
three stages, records `FAILED` in the trace, and leaves the corresponding
`claim.*_result` field `None` — but `claim.status` stays `PROCESSING` and
the pipeline keeps going. A `PolicyEngine` failure must never silently
read as "approved" (nothing reads a `None` result as a pass), and a
`FinancialCalculationService` failure must never guess a payable amount —
both failure modes are "we don't know," reported honestly, not "we
assumed the best case."

### Financial calculation ordering and the "never trust extracted amounts as authoritative" rule
`FinancialCalculationService.calculate()` follows one fixed order:
eligible amount → network discount → remaining annual-OPD-allowance cap →
copay deduction → payable amount. `sub_limit` and `per_claim_limit` are
surfaced on the result for transparency but are deliberately **not**
applied as payable-amount caps (a Phase 3 correctness fix — see
`docs/tradeoffs.md` "Phase 3 Correctness Pass": `test_cases.json`'s own
official TC006/TC010 worked amounts both pay out the full discount/copay-
adjusted figure despite exceeding these limits; `per_claim_limit` is
instead a whole-claim REJECT gate evaluated by `DecisionGenerationAgent`,
see below). Every step is logged into a human-readable `calculation_steps`
list so the full chain is auditable without re-deriving it. An AI-extracted
hospital-bill total is never substituted for the claimed/eligible amount —
a mismatch beyond a small tolerance is recorded as a `warnings` entry
naming both values (see "Bill Amount Reconciliation" in `docs/tradeoffs.md`),
never silently "corrected."

### Fraud architecture — deterministic thresholds first, AI reserved but unused
`FraudAnalysisAgent` reads four thresholds from `policy_terms.json`'s
`fraud_thresholds` (same-day claim count, monthly claim count, high-value,
auto-manual-review) and counts matching historical claims via
`ClaimRepository.list_by_member()` (or `submission.claims_history` for
evaluation fixtures — the same "one shape, two sources" pattern
`DocumentInputAdapter` established in Phase 2A). `FraudAnalysisResult`
reserves an `ai_risk_score: Optional[float]` field, deliberately unused
(`None`) in this phase and deliberately kept separate from
`deterministic_thresholds_triggered` — a future AI-assisted risk signal
must never be merged into the deterministic list, so a human reviewer can
always tell "the policy-defined threshold was crossed" from "the model
thought this looked suspicious." This phase doesn't add a third AI-calling
component because every signal the assignment asks for here is already
exact and policy-defined; an AI call would add cost and a new failure mode
without a rule it's actually needed to satisfy.

### Scaling considerations
All three stages are pure computation over already-fetched data (no new
AI calls, at most one extra DB query for `FraudAnalysisAgent`'s history
lookup) — they add negligible latency compared to Phase 2A/2B's AI-bound
stages. `FraudAnalysisAgent.list_by_member()` is a lightweight
columns-only SQL query (not a full `Claim` reconstruction per historical
row), scoped to avoid the N+1-ish cost of rehydrating full domain objects
just to read `date`/`amount`/`provider`. As claim volume grows, the
same-day/monthly counting queries are the part most worth indexing
(`member_id` + `treatment_date`) — not addressed here since SQLite/dev
scale doesn't need it yet, but flagged for whenever PostgreSQL is adopted
(see Phase 0's SQLite-vs-PostgreSQL trade-off).

### How explainability is preserved
Same rules as every prior phase (§6): one `STARTED`→`COMPLETED`/`FAILED`/
`SKIPPED` trace pair per stage, metadata summarising outcomes
(`covered`/`failed_rules`, `payable_amount`/caps-applied, `risk_level`/
`flags_count`) — never the full findings list or calculation-steps audit
trail duplicated into the trace row (those live on the persisted result,
which already has its own storage lifecycle). The three new
`_DOWNSTREAM_OF`-driven `SKIPPED` markers (see `docs/component-contracts.md`
"ClaimsPipeline — Phase 2C stages") mean a claim that stops at, say,
`CROSS_DOCUMENT_VALIDATION` shows all three Phase 2C stages explicitly
`SKIPPED` in its trace — never silently absent, which would look
indistinguishable from "this trace is incomplete."

---

## Decision Generation & Explanation (Phase 2D)

Phase 2D adds the final two pipeline stages — `DecisionGenerationAgent`,
`ExplanationAgent` — closing the loop assignment.md point 4 requires: a
claim must reach one of `APPROVED`/`PARTIAL`/`REJECTED`/`MANUAL_REVIEW`,
with an approved amount, a reason, and a confidence score.

```
... Fraud Analysis (Phase 2C)
                │
                ▼
    DecisionGenerationAgent (deterministic — combines Policy/Financial/
    Fraud/Extraction results already computed; makes zero AI calls)
                │
                ▼
    status = DECIDED, claim.decision set (always, even on internal failure)
                │
                ▼
    ExplanationAgent (real AIProvider call, writes up the decision in
    plain language; never invents/recalculates/overrides anything)
                │
                ▼
    claim.decision.explanation_detail set (AI-written, or a deterministic
    fallback if the call failed — either way, never empty)
```

### Deterministic decision authority — why the LLM never decides

The same Core Principle 3 that already governs Policy/Financial/Fraud
(§ "Deterministic Policy Evaluation" above) extends to the decision
itself: `DecisionGenerationAgent` makes zero AI calls and performs no
independent calculation — it is a pure function of `claim.
policy_evaluation_result`/`financial_calculation_result`/
`fraud_analysis_result`, all already computed deterministically by Phase
2C. An LLM choosing `APPROVED` vs `REJECTED`, or inventing an approved
amount, would mean the single most consequential output of the entire
system — money paid or not paid to a real person — rests on a
non-deterministic, unauditable judgment call. That is exactly the "black
box" outcome assignment.md point 5 rules out. `ExplanationAgent`'s LLM
call happens **after** the decision already exists and is passed the
finished `ClaimDecision` as an immutable input it must explain, never
influence — see `app/ai/prompts/explanation.py`'s system prompt for the
explicit, enumerated constraints ("do not calculate amounts," "do not
override the decision," ...) enforcing this boundary at the prompt level,
backed by Pydantic validation of the response and a deterministic
fallback if anything about the call can't be trusted.

### Decision precedence — deterministic, not LLM-invented

`DecisionGenerationAgent` implements a fixed, ordered precedence (full
derivation and worked numbers in `docs/tradeoffs.md` "Decision
Precedence"): insufficient evidence → `MANUAL_REVIEW`; claim-level policy
exclusion/waiting-period/pre-authorization-missing (collected together,
not first-match-wins) → `REJECTED`; a whole-claim per-claim-limit breach
with no line-item-driven partial eligibility to fall back on (Rule 5.5,
Phase 3) → `REJECTED`; fraud-driven manual-review threshold →
`MANUAL_REVIEW`; zero payable → `REJECTED`; genuine line-item exclusion
(DENTAL/VISION only) → `PARTIAL`; otherwise → `APPROVED`; low aggregate
confidence even at that point → downgrade to `MANUAL_REVIEW` while still
surfacing the reliable financial figure. A Phase 3 audit resolved what was
previously a disclosed discrepancy here: TC006/TC008/TC010 are now
reproduced exactly (decision and amount) by reading `per_claim_limit` as a
reject gate rather than a cap, and `sub_limit` as informational only — see
`docs/tradeoffs.md` "Phase 3 Correctness Pass" and `docs/eval-report.md`
for the resulting 12/12 official-case match.

### Financial ordering is unchanged — Decision Generation only reads the result

`FinancialCalculationService`'s calculation order (discount → limits →
copay, established in Phase 2C) is not touched by Phase 2D.
`DecisionGenerationAgent` reads exactly one number from it —
`payable_amount` — and never recomputes, rounds, or adjusts it. The
`PARTIAL`/`APPROVED` distinction is based on `eligible_amount` vs.
`claimed_amount` (whether the *content* of the claim was partially
ineligible), deliberately not on whether `payable_amount` is merely lower
than `claimed_amount` — copay and network discount both do that for a
perfectly normal, fully-`APPROVED` claim (TC004, TC010), so using a raw
amount comparison would have misclassified them as `PARTIAL`.

### Failure handling — two different guarantees for two different reasons

`DecisionGenerationAgent` and `ExplanationAgent` fail differently on
purpose:

- **Decision Generation** is pure deterministic Python with no I/O — it
  should never raise. If it somehow does, the pipeline substitutes a
  conservative `MANUAL_REVIEW` fallback decision (`_fallback_decision()`)
  rather than leaving `claim.decision` empty, because assignment.md point
  4 requires a decision to exist for any claim that reaches this stage —
  unlike Policy/Financial/Fraud, there is no legitimate "we don't have
  this result" state once Decision Generation has been attempted.
- **Explanation** makes a real network call and is expected to fail
  sometimes (rate limits, timeouts, the SSL/network issue documented in
  Known Issues). Its entire contract is "never raise" — a single broad
  `try/except` around evidence-building, the request, and response
  validation guarantees a valid `ExplanationResult` always comes back,
  with `source=FALLBACK` distinguishing a degraded response from a
  genuine AI one. The pipeline's own `try/except` around the call is
  defense-in-depth only; verified against a real, live SSL failure in
  this environment (not just a simulated one) — see `docs/AI_HANDOFF.md`
  "Verification (Phase 2D)".

Either way, a failure in Explanation can never retroactively change or
invalidate the decision Stage 8 already produced — the two stages are
sequenced specifically so a later failure cannot corrupt an earlier,
already-persisted result.

### Confidence strategy

`DecisionGenerationAgent._compute_confidence()` takes the minimum
confidence across every upstream stage that actually ran (the same
"never fabricate, minimum wins" convention Phase 2B/2C already
established for `ClaimExtractionResult`/`PolicyEvaluationResult`), then
applies an additional penalty for each stage that produced **no result at
all** (missing is treated as strictly worse than merely low-confidence).
This is a deterministic, operational heuristic — explicitly not claimed
to be statistically calibrated — see `docs/tradeoffs.md` "Decision
Confidence Strategy" for the exact formula, the chosen threshold, and why
a single degraded non-critical component (Fraud Analysis alone, as in
TC011) must not by itself force a claim into `MANUAL_REVIEW`.

### Why the LLM is not allowed to calculate financial values

Beyond the general "deterministic authority" principle above: an LLM
computing money is a compliance and auditability risk specific to
insurance — every payable-amount figure must be reproducible from
`policy_terms.json`'s stated rules by a human auditor without needing to
re-run a model, and a non-deterministic financial calculation would mean
two audits of the same claim could legitimately disagree. `Financial
CalculationService` (Phase 2C) already made this guarantee for the
payable amount; Phase 2D extends it to the final decision built on top of
that amount, and the explanation LLM is explicitly instructed never to
restate the approved amount as anything other than the exact figure it
was given.

### Scaling considerations

Both new stages are pure computation or a single external API call, same
profile as the rest of the pipeline: `DecisionGenerationAgent` adds
negligible latency (no I/O). `ExplanationAgent` adds one real LLM call
per claim — the same latency/cost profile as Phase 2A's classification
call, and subject to the same not-yet-parallelized, no-retry limitations
already tracked as Known Issues 11/15/16. At 10x load, the explanation
call is the one part of this phase worth reconsidering first: batching
multiple claims' explanations into fewer calls, or making the call
optional/deferred (compute the decision synchronously, generate the
explanation asynchronously afterward) would reduce per-claim latency
without weakening the deterministic guarantees above, since Explanation
never gates the decision already produced.

---

## Component Map

### Backend Layers

```
┌────────────────────────────────────────────────────────────────┐
│  API Layer (FastAPI)                                           │
│  app/api/v1/health.py   ← GET /api/v1/health                 │
│  app/api/v1/traces.py   ← GET /api/v1/claims/{id}/trace      │
│  app/api/v1/claims.py   ← POST /claims, GET /claims/{id}     │
│  app/api/v1/schemas.py  ← ClaimResponse (API contract)        │
│  app/api/deps.py        ← Dependency injection                │
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  Services Layer (Phase 2A)                                     │
│  app/services/document_input_adapter.py                        │
│      ← DocumentInputAdapter: request -> (Claim, classifications)│
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  Pipeline Layer (Phase 2A)                                      │
│  app/pipeline/pipeline.py  ← ClaimsPipeline orchestrator       │
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  Agent Layer (Phase 2A + 2B)                                     │
│  app/agents/base_agent.py   ← BaseAgent(ai_provider=None|AI)  │
│  app/agents/claim_validation_agent.py                          │
│  app/agents/document_verification_agent.py                     │
│  app/agents/cross_document_validation_agent.py                 │
│  app/agents/document_extraction_agent.py  ← Phase 2B          │
│  (fraud_analysis_agent, decision_generation_agent, ... ← planned)│
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  AI Layer                                                      │
│  app/ai/providers/base.py            ← AIProvider ABC         │
│  app/ai/providers/gemini_provider.py     ← Gemini adapter     │
│  app/ai/providers/anthropic_provider.py  ← Anthropic adapter  │
│  app/ai/schemas/ai_schemas.py        ← Request/response types │
│  app/ai/prompts/document_verification.py ← classification prompt│
│  app/ai/prompts/extraction_common.py     ← shared extraction rules/schema helpers│
│  app/ai/prompts/prescription_extraction.py    ← Phase 2B      │
│  app/ai/prompts/hospital_bill_extraction.py   ← Phase 2B      │
│  app/ai/prompts/lab_report_extraction.py      ← Phase 2B      │
│  app/ai/prompts/pharmacy_bill_extraction.py   ← Phase 2B      │
│  app/ai/prompts/dental_extraction.py          ← Phase 2B      │
│  app/ai/prompts/discharge_summary_extraction.py ← Phase 2B    │
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  Tracing Layer (Phase 1)                                       │
│  app/domain/trace.py     ← TraceEvent, TraceContext (models)  │
│  app/tracing/service.py  ← TraceService (span/started/...)    │
│  app/tracing/logging.py  ← Structured application logging     │
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  Domain / Policy Layer                                          │
│  app/domain/models.py        ← Claim, Member, Document        │
│  app/domain/verification.py  ← Validation/Verification results│
│  app/domain/extraction.py    ← Extraction schemas (Phase 2B)  │
│  app/domain/errors.py        ← Error hierarchy                │
│  app/policy/policy_repository.py ← PolicyRepository (Phase 2A)│
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  Infrastructure Layer                                          │
│  app/repositories/database.py         ← Async SQLAlchemy      │
│  app/repositories/base.py             ← Repository ABC        │
│  app/repositories/trace_models.py     ← TraceEventORM         │
│  app/repositories/trace_repository.py ← TraceRepository       │
│  app/repositories/claim_models.py     ← ClaimORM, ClaimDocumentORM │
│  app/repositories/claim_repository.py ← ClaimRepository       │
│  app/config/settings.py               ← Pydantic BaseSettings │
│  app/config/paths.py                  ← Source-file resolution│
└────────────────────────────────────────────────────────────────┘
```

---

## Database Design

**Phase 0**: SQLite via aiosqlite (development default)
**Phase 1**: Added `trace_events` table (see `app/repositories/trace_models.py`) — one row per `TraceEvent`, ordered by autoincrement `id`, indexed on `(claim_id, id)` and `(trace_id, id)` for the two access patterns `TraceRepository` supports.
**Phase 2A**: Added `claims` (one row per claim; validation/verification results stored as JSON columns, same pattern as `trace_events.metadata_json`) and `claim_documents` (child rows keyed by `claim_id`) — see `app/repositories/claim_models.py`.
**Phase 2B**: No new table — extended the existing `claims`/`claim_documents` rows instead (hybrid persistence, see `docs/component-contracts.md` "ClaimRepository — Extraction persistence"): `claims.extraction_summary_json` (claim-level rollup) and seven new `claim_documents` columns (`extraction_status`, `diagnosis`, `treatment`, `document_date`, `doctor_name`, `total_amount`, `extraction_json`). A dedicated `extractions` table was considered and rejected — `claim_documents` is already one-row-per-document, so a second document-scoped table would just be a 1:1 join for no benefit.
**Phase 3 (planned)**: Add an ORM model for `decisions`.

**Migration path**: Changing `DATABASE_URL` to a PostgreSQL `asyncpg://` URL requires no code changes. The SQLAlchemy ORM is database-agnostic.

**Registration note**: SQLAlchemy declarative classes register with `Base.metadata` at import time, not at table-creation time. `init_database()` explicitly imports `app.repositories.trace_models` before calling `Base.metadata.create_all` — any future ORM module needs the same explicit import there, or its table silently won't be created.

**No migration story**: `Base.metadata.create_all` only creates tables that don't exist — it never alters an existing table's columns. Adding columns to `ClaimDocumentORM` during the Real Document Upload correction (`mime_type`, `size_bytes`, `storage_reference`, `patient_name`, `confidence`, `processing_status`) required deleting the gitignored local dev `data/claims.db` so it would regenerate with the new schema; a real migration tool (e.g. Alembic) would be needed before this matters for a deployed environment with data worth preserving across schema changes.

---

## AI Provider Interface

The `AIProvider` ABC exposes three core capabilities:

| Method | Use Case | Output |
|--------|----------|--------|
| `generate_text()` | Explanations, member messages | `AIGenerateResponse` |
| `generate_structured()` | Extraction, classification | `AIStructuredResponse` |
| `analyze_document()` | Real multimodal classification (Phase 2A) and structured extraction (Phase 2B) of uploaded document bytes | `DocumentAnalysisResponse` |

Structured output uses Anthropic's `tool_use` feature (or Gemini's
`response_schema`/`response_mime_type=application/json`) to guarantee JSON
schema conformance, depending on which provider is configured. Phase 2B's
six extraction schemas (`app/ai/prompts/*_extraction.py`) are the second
real proof of this abstraction, after Phase 2A's classification schema —
`DocumentExtractionAgent` never imports `google.genai` or `anthropic`, and
switching `AI_PROVIDER=gemini` → `AI_PROVIDER=anthropic` requires no
change to the agent or any schema.

---

## Frontend Architecture

```
src/
├── App.tsx                       ← Router + layout shell
├── main.tsx                      ← React entry point
├── types/index.ts                ← TypeScript types (mirrors backend models)
├── services/api.ts               ← API client abstraction (systemApi, traceApi, claimsApi)
├── hooks/useHealth.ts            ← Data-fetching hook
├── hooks/useClaimTrace.ts        ← Data-fetching hook for claim trace events
├── components/TraceViewer.tsx    ← Reusable trace-event timeline component
├── pages/Dashboard.tsx           ← System health dashboard
├── pages/ClaimSubmission.tsx     ← Real file-upload claim submission form
└── pages/ClaimDetail.tsx         ← Claim result + per-document AI results + extraction + TraceViewer
```

**Phase 2B**: `ClaimDetail.tsx`'s `DocumentCard` gained a collapsed-by-default
"Extracted Information" toggle (only rendered when `doc.extraction` is
present) and a new `ExtractedInfo` component that switches on
`extraction.extraction.document_type` to render a document-type-specific,
operations-friendly layout — never raw JSON as the primary view. `types/index.ts`
gained the matching TypeScript mirror of `app/domain/extraction.py`
(`ExtractionPayload` as a discriminated union on `document_type`, same
shape as the backend's `Annotated[Union[...], Field(discriminator=...)]`).

All backend calls go through `services/api.ts`. Components never call `fetch()` directly.
`claimsApi.submit()` is the one method that talks multipart: it builds a
`FormData` from real `File` objects and posts it via `requestMultipart()`
(deliberately omits a `Content-Type` header so the browser sets the
multipart boundary itself — setting it manually breaks the request).

`TraceViewer` is pure presentation — it takes `events: TraceEvent[]` as a
prop and renders them; it does not fetch data itself and has no notion of
"claim". `useClaimTrace` is the data-fetching counterpart. Both are mounted
in `ClaimDetail.tsx` against real backend data.

`ClaimSubmission.tsx` has no document-type field of any kind — a hidden
`<input type="file" accept=".pdf,.jpg,.jpeg,.png" multiple>`, triggered by
a styled "+ Add Document" button, is the only way to attach documents.
Selected files are held in local component state (name/size/the `File`
object itself) and rendered as a list with a Remove control per file; the
component also runs its own type/emptiness validation before submit as
defense-in-depth against a bypass of the input's `accept` filter (e.g.
drag-and-drop, or "All Files" in the OS picker) — the backend's
`validate_upload()` is still the authoritative check. `ClaimDetail.tsx`'s
`DocumentCard` renders each document's AI-determined Type/Quality/Patient/
Confidence exactly as the API returns them; nothing is inferred or
guessed client-side.

---

## Configuration Architecture

Settings are loaded once via `get_settings()` (lru_cache singleton):

```python
APP_ENV → Environment enum
AI_PROVIDER → AIProvider enum → Provider factory → Concrete provider
ANTHROPIC_API_KEY / GEMINI_API_KEY → Injected into the concrete provider (never hardcoded)
DATABASE_URL → SQLAlchemy engine (swappable)
```

Per-agent model overrides are supported by passing `model` in `AIGenerateRequest` — no routing infrastructure needed yet.

`.env` is resolved from the project root regardless of the process's
working directory (see `app/config/settings.py` — `_PROJECT_ROOT_ENV_FILE`),
so `uvicorn` behaves the same whether launched from `backend/` or the
project root.

### Source-of-truth file resolution (Phase 2A)
`policy_terms.json`, `test_cases.json`, and `sample_documents_guide.md`
actually live at the **repository root** — one level above
`multi_agent_claims_pipeline/` — not inside the project directory as the
originally documented tree in `README.md`/`docs/AI_HANDOFF.md` assumed.
`app/config/paths.py`'s `resolve_source_file()` checks the CWD, the
project root, and the repo root, in that order, so `PolicyRepository` and
the evaluation runner find these files regardless of where a process is
launched from — the same problem `.env` resolution solved in Phase 1,
generalised into one shared helper.

---

## Error Flow

```
Exception at any component
         │
         ▼
ClaimsSystemError subclass
    .recoverable = True/False
    .code = "SPECIFIC_CODE"
    .details = {...}
         │
         ├──────────────────────────────┐
         ▼                              ▼
Pipeline Orchestrator (Phase 2)   TraceService.failed(component, exc)
    if recoverable:                   │
        log + skip component +        ▼
        lower confidence          error_info_from_exception(exc)
    else:                             │  preserves .recoverable exactly
        stop pipeline +               ▼
        return error response    TraceErrorInfo → persisted TraceEvent
         │
         ▼
API Exception Handler (app/main.py)
    → Structured JSON error response
```

---

## Scaling to 10x Load

Plum's own framing (75,000 claims/year today, a path to 10 million lives
by 2030) is the explicit lens assignment.md asks this document to answer
through. The per-phase "Scaling considerations" notes above cover each
stage's own bottleneck; this section consolidates the answer end to end.

**What would break first — sequential per-document AI calls.** Document
Verification and Document Extraction each make one Gemini call per
uploaded document, awaited in sequence (Known Issues 15/16). A 3-document
claim today costs ~60-120s of wall-clock AI latency before a member sees
a result. At 10x submission volume this is the first thing that would
need to change — not because any single claim gets slower, but because
throughput is bounded by how many of these sequential calls can be in
flight at once. **Fix**: `asyncio.gather()` the per-document calls within
a stage (they're already independent — no document's classification
depends on another's), and consider a bounded worker pool / job queue
(e.g. Celery/arq backed by Redis) so claim submission returns immediately
with a "processing" status and the pipeline runs asynchronously, polled
or pushed via websocket — a natural fit since `TraceService` already
produces a structured, replayable event stream per claim.

**Database — SQLite's single-writer limitation.** SQLite (Phase 0's
deliberate 2-3-day-assignment choice, see `docs/tradeoffs.md`) serializes
writes; at meaningfully concurrent claim volume this becomes the ceiling
long before 10x. **Fix**: the `DATABASE_URL` env var already makes
PostgreSQL a connection-string change, not a rewrite — every query goes
through SQLAlchemy's async ORM, no raw SQLite-specific syntax anywhere in
`app/repositories/`. Add connection pooling (`asyncpg` + SQLAlchemy's
pool settings) and the `member_id`/`treatment_date` index
`FraudAnalysisAgent`'s same-day/monthly counting queries would benefit
from (noted but not needed at current scale — see "Fraud architecture"
above).

**Document storage — local disk doesn't scale horizontally.**
`LocalFileDocumentStorage` (Phase 2A) writes under a local `data/uploads/`
directory — fine for one process, but multiple API instances behind a
load balancer would each have their own, inconsistent local disk.
**Fix**: `DocumentStorage` is already an abstract interface
(`app/storage/document_storage.py`) with exactly two methods
(`save`/`read`) — an S3-backed implementation (Known Issue 14) is a new
class behind the same interface, no change needed anywhere that calls it.

**AI provider rate limits and cost.** 10x claim volume means 10x Gemini
calls. `AIProvider` is already fully abstracted (Core Principle 2 — no
agent imports a vendor SDK directly), so spreading load across providers,
adding response caching for identical documents (unlikely but cheap to
guard against), or negotiating a higher rate limit are all changes
confined to `app/ai/providers/` and configuration, never the agents that
call it.

**Stateless API layer — already horizontally scalable.** FastAPI workers
hold no in-process state between requests (every result is read from/
written to the database via `ClaimRepository`); running N `uvicorn`
workers behind a load balancer requires no code change, only
infrastructure — the one prerequisite is the PostgreSQL migration above,
since N SQLite-writing processes would corrupt each other.

**Trace volume.** Every pipeline stage writes at least one `TraceEvent`
row; at 10x claim volume this is 10x row volume, but each row is small,
append-only, and never updated — a good fit for time-series partitioning
or a dedicated event-store table (or, at genuinely large scale, an
external log/observability system) if it ever became the actual
bottleneck, which sequential AI calls (above) would hit first.

**What would NOT need to change**: the deterministic Policy/Financial/
Fraud/Decision layer (Phase 2C/2D) — pure Python computation over
already-fetched data, negligible cost per claim regardless of volume; the
multi-agent architecture itself, since each agent already has a single,
narrow responsibility and no hidden cross-agent coupling to untangle
under load.
