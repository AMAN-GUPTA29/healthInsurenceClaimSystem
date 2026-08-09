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
│  Agent Layer (Phase 2A)                                         │
│  app/agents/base_agent.py   ← BaseAgent(ai_provider=None|AI)  │
│  app/agents/claim_validation_agent.py                          │
│  app/agents/document_verification_agent.py                     │
│  app/agents/cross_document_validation_agent.py                 │
│  (extraction_agent, fraud_analysis_agent, ... ← planned)       │
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  AI Layer                                                      │
│  app/ai/providers/base.py            ← AIProvider ABC         │
│  app/ai/providers/gemini_provider.py     ← Gemini adapter     │
│  app/ai/providers/anthropic_provider.py  ← Anthropic adapter  │
│  app/ai/schemas/ai_schemas.py        ← Request/response types │
│  app/ai/prompts/document_verification.py ← classification prompt│
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
**Phase 2B/3**: Add ORM models for:
- `extractions` table
- `decisions` table

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
| `analyze_document()` | Real multimodal classification of uploaded document bytes (type/quality/patient/confidence) | `DocumentAnalysisResponse` |

Structured output uses Anthropic's `tool_use` feature (or Gemini's
`response_schema`/`response_mime_type=application/json`) to guarantee JSON
schema conformance, depending on which provider is configured.

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
└── pages/ClaimDetail.tsx         ← Claim result + per-document AI results + TraceViewer
```

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
