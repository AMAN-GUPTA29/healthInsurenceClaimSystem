# Component Contracts

This document defines the interface contract for every significant component in the system.
It will be updated at the end of each phase as new components are implemented.

**Format**: Each contract includes inputs, outputs, guarantees, and failure modes.

---

## AIProvider (Abstract Interface)

**File**: `app/ai/providers/base.py`

### Contract

| Property | Value |
|----------|-------|
| Interface type | Abstract Base Class (ABC) |
| Async | Yes — all methods are `async` |
| Vendor dependency | None — only domain types |
| Error types | `AITimeoutError`, `AIRateLimitError`, `AIAuthenticationError`, `AIResponseParseError`, `AIProviderError` |

### Methods

#### `generate_text(request: AIGenerateRequest) -> AIGenerateResponse`
- **Input**: Messages, optional system prompt, model/temperature overrides
- **Output**: Text string + usage metadata + latency
- **Guarantees**: Never raises vendor-specific exceptions; always translates to domain errors
- **Failure**: `AITimeoutError` | `AIRateLimitError` | `AIProviderError`

#### `generate_structured(request: AIStructuredRequest) -> AIStructuredResponse`
- **Input**: Messages + JSON Schema describing expected output
- **Output**: Parsed dict guaranteed to match schema + usage metadata
- **Guarantees**: `response.data` always parses against `request.output_schema`
- **Failure**: `AIResponseParseError` if parsing fails, otherwise as above

#### `analyze_document(request: DocumentAnalysisRequest) -> DocumentAnalysisResponse`
- **Input**: Base64 image(s)/PDF(s) + text prompt, optional output schema
- **Output**: Text analysis and/or structured data
- **Guarantees**: Handles multipage PDFs and multiple images in a single call
- **Failure**: As above

---

## AnthropicProvider

**File**: `app/ai/providers/anthropic_provider.py`

| Property | Value |
|----------|-------|
| Implements | `AIProvider` |
| SDK | `anthropic` (only file where this is imported) |
| Structured output | Via `tool_use` feature |
| PDF support | Native (Claude 3+ document blocks) |

---

## BaseAgent

**File**: `app/agents/base_agent.py`

### Contract

| Property | Value |
|----------|-------|
| Interface type | ABC |
| AI dependency | `AIProvider` via constructor injection |
| SDK imports | **None** — uses only `AIProvider` interface |
| Error output | Domain errors from `app/domain/errors` |

### Guarantee
No subclass of `BaseAgent` may import any AI vendor SDK.
The `ai_provider` property exposes only the `AIProvider` interface.

---

## Settings

**File**: `app/config/settings.py`

### Contract

| Property | Value |
|----------|-------|
| Source | Environment variables / `.env` file |
| Singleton | Yes (`lru_cache`) |
| Secret handling | API keys are `Optional[str]`; never hardcoded |
| Test override | Call `get_settings.cache_clear()` before patching env |

### Key fields

| Field | Type | Description |
|-------|------|-------------|
| `ai_provider` | `AIProvider` enum | Which vendor to use |
| `ai_model` | `str` | Model identifier (e.g. `claude-sonnet-4-5`) |
| `anthropic_api_key` | `Optional[str]` | Required for Anthropic provider |
| `database_url` | `str` | SQLAlchemy async URL |
| `cors_origins` | `List[str]` | Allowed frontend origins |

---

## Domain Models

**File**: `app/domain/models.py`

### Claim (Lifecycle)

```
ClaimSubmission   →  Claim  →  ClaimDecision
    (input)        (enriched)     (output)
```

| Model | Immutable | DB-mapped |
|-------|-----------|-----------|
| `ClaimSubmission` | Yes (input only) | No |
| `Claim` | No (enriched by pipeline) | Phase 1 |
| `ClaimDecision` | Yes (final output) | Phase 1 |
| `Member` | Yes (from policy JSON) | No |
| `Document` | No (enriched by extraction) | Phase 1 |

### Key Guarantees
- `ClaimSubmission.treatment_date` cannot be in the future
- `ClaimSubmission.claimed_amount` must be > 0
- `ClaimDecision.confidence_score` is in [0.0, 1.0]
- `Document.effective_type` prefers `detected_type` over `declared_type`

---

## Error Hierarchy

**File**: `app/domain/errors.py`

```
ClaimsSystemError
├── ValidationError
│   ├── ClaimValidationError
│   ├── MemberNotFoundError
│   └── PolicyNotFoundError
├── DocumentError
│   ├── DocumentUnreadableError        (recoverable)
│   ├── DocumentTypeMismatchError      (recoverable)
│   ├── DocumentPatientMismatchError   (recoverable)
│   ├── MissingRequiredDocumentError   (recoverable)
│   └── ExtractionError               (recoverable)
├── AIProviderError
│   ├── AITimeoutError                 (recoverable)
│   ├── AIRateLimitError               (recoverable)
│   ├── AIAuthenticationError          (NOT recoverable)
│   ├── AIResponseParseError           (recoverable)
│   └── AIProviderNotConfiguredError   (NOT recoverable)
├── PolicyError
│   ├── PolicyLoadError                (NOT recoverable)
│   └── PolicyEvaluationError
├── PipelineError
│   └── ComponentFailureError          (recoverable)
└── InfrastructureError
    └── DatabaseError                  (NOT recoverable)
```

### Recoverable vs Non-Recoverable

- **Recoverable** (`recoverable=True`): Pipeline can skip the component, lower confidence, and continue
- **Non-Recoverable** (`recoverable=False`): Pipeline must stop; no partial decision can be made

---

## Trace Domain Models

**File**: `app/domain/trace.py`

Pure Pydantic models — no database, no FastAPI, no AI SDK imports, matching
the same rule as `app/domain/models.py`.

### TraceComponent (enum)

Closed vocabulary identifying which pipeline component produced an event:
`CLAIM_VALIDATION`, `DOCUMENT_VERIFICATION`, `DOCUMENT_EXTRACTION`,
`CROSS_DOCUMENT_VALIDATION`, `POLICY_ENGINE`, `FRAUD_ANALYSIS`,
`FINANCIAL_CALCULATION`, `DECISION_GENERATION`, `EXPLANATION`, `PIPELINE`.

No component may invent a free-form string identifier instead of using this enum.

### TraceEventType (enum)

Closed vocabulary for event status: `STARTED`, `COMPLETED`, `FAILED`,
`SKIPPED`, `WARNING`.

### TraceContext

| Property | Value |
|----------|-------|
| Fields | `trace_id: str` (auto-generated), `claim_id: str` |
| Construction | `TraceContext.new(claim_id=...)` — always mints a fresh `trace_id` |
| Guarantee | Immutable value object; pass explicitly through constructor injection, never stash in module/global state |

### TraceEvent

| Field | Type | Notes |
|-------|------|-------|
| `event_id` | `str` | Auto-generated (uuid4 hex) |
| `trace_id`, `claim_id` | `str` | Copied from the owning `TraceContext` |
| `component` | `TraceComponent` | Required |
| `event_type` | `TraceEventType` | Required |
| `message` | `str` | Defaults to `""` |
| `timestamp` | `datetime` | Auto-generated (UTC) at construction |
| `duration_ms` | `Optional[float]` | `>= 0`; only set on COMPLETED/FAILED via `span()` or explicit call |
| `confidence` | `Optional[float]` | `[0.0, 1.0]`; **never invented** — left `None` if the component has no natural confidence score |
| `metadata` | `Dict[str, Any]` | Structured, summarized facts only — never full documents/prompts/responses; passed through `redact_metadata` before being attached |
| `error` | `Optional[TraceErrorInfo]` | Set only on FAILED |
| `ai_metadata` | `Optional[AITraceMetadata]` | Set only when the component made an AI call |
| `sequence` | `Optional[int]` | `None` until read back from `TraceRepository`; then reflects DB insertion order — the authoritative ordering key |

### TraceErrorInfo / `error_info_from_exception`

| Property | Value |
|----------|-------|
| Fields | `error_type: str`, `code: Optional[str]`, `message: str`, `recoverable: bool` |
| Guarantee | Preserves `ClaimsSystemError.recoverable` exactly — tracing never changes the orchestrator's stop-vs-continue semantic |
| Safety | Never includes stack traces, exception args, or arbitrary attributes — only `type(exc).__name__`, `.code`, `.message`, `.recoverable` |
| Non-`ClaimsSystemError` input | Falls back to `code=None`, `recoverable=False` |

### AITraceMetadata

All fields optional (`provider`, `model`, `latency_ms`, `input_tokens`,
`output_tokens`) — a provider that can't report token counts simply omits
them rather than the interface being forced to guarantee something it can't
deliver. Never carries prompts, responses, or API keys.

---

## TraceService

**File**: `app/tracing/service.py`

### Contract

| Property | Value |
|----------|-------|
| Construction | `TraceService(context: TraceContext, *, sink: Optional[TraceSink] = None)` |
| Async | Yes — every recording method is `async` |
| Persistence | Optional via `sink` (anything with `async def record(event)`, e.g. `TraceRepository`). Without a sink, events are still collected in `.events` — zero database setup needed for unit tests |
| Dependencies | `app.domain.trace`, `app.tracing.logging` — no database import, no FastAPI import |

### Methods

| Method | Emits | Notes |
|--------|-------|-------|
| `started(component, message="", *, metadata=None)` | `STARTED` | |
| `completed(component, *, message="", duration_ms=None, confidence=None, metadata=None, ai_metadata=None)` | `COMPLETED` | |
| `failed(component, error, *, message="", duration_ms=None, metadata=None)` | `FAILED` | Converts `error` via `error_info_from_exception` |
| `warning(component, message, *, metadata=None)` | `WARNING` | |
| `skipped(component, message="", *, metadata=None)` | `SKIPPED` | |
| `span(component, *, message="", metadata=None)` | `STARTED` on enter, `COMPLETED`/`FAILED` on exit | Async context manager; captures `duration_ms` automatically; re-raises the original exception after recording `FAILED` |

### Guarantees
- Every emitted event's `metadata` has been passed through `redact_metadata` — keys containing `api_key`, `apikey`, `secret`, `token`, `password`, or `authorization` (case-insensitive substring match) are replaced with `"[REDACTED]"` before the event is ever constructed. Legitimate business fields (`policy_rule`, `primary_member_id`, etc.) are left untouched.
- Never invents a `confidence` value — components that don't naturally produce one simply omit the argument.
- If a `sink` is provided, every event is persisted immediately (awaited) as it's emitted — no batching, no risk of losing events on an unhandled crash between emission and persistence.
- `create_trace_service(claim_id, *, persist=True)` is a convenience factory (wires a fresh `TraceContext` + `TraceRepository` sink) for future pipeline code; pass `persist=False` for a database-free tracer.

### Failure behavior
`TraceService` itself does not raise domain errors under normal operation — persistence failures propagate as whatever `TraceRepository`/SQLAlchemy raises (not yet wrapped in a domain error type; a `TracingError` wrapper is a candidate for Phase 2 if tracing needs its own graceful-degradation story separate from the component it's observing).

---

## TraceRepository

**File**: `app/repositories/trace_repository.py`

### Contract

| Property | Value |
|----------|-------|
| Shape | **Not** `BaseRepository[T, ID]` — trace events are a one-claim-to-many-events relationship, not single-entity CRUD-by-id, so the generic `get_by_id`/`save`/`delete` shape doesn't fit. Deliberate deviation, not an oversight. |
| Async | Yes |
| DB access | Each method opens its own session via `app.repositories.database.get_session()`, consistent with the rest of the database foundation |
| ORM | `app.repositories.trace_models.TraceEventORM` — the only place that type is used outside this file |

### Methods

| Method | Input | Output | Notes |
|--------|-------|--------|-------|
| `create_event(event: TraceEvent)` | Domain `TraceEvent` | `None` | Also aliased as `record()` so an instance structurally satisfies `TraceService`'s `TraceSink` protocol without an adapter class |
| `list_by_trace_id(trace_id: str)` | — | `List[TraceEvent]` | Ordered by DB-assigned `id` (insertion order) |
| `list_by_claim_id(claim_id: str)` | — | `List[TraceEvent]` | Ordered by DB-assigned `id`; unknown `claim_id` returns `[]`, not an error |

### Guarantee
Ordering is preserved via the autoincrement primary key (`TraceEventORM.id`), not `timestamp` — two events emitted in the same millisecond at pipeline speed would otherwise be ambiguous. `TraceEvent.sequence` is populated from this column on read.

---

## Trace API — `GET /api/v1/claims/{claim_id}/trace`

**File**: `app/api/v1/traces.py`

### Contract

| Property | Value |
|----------|-------|
| Method | `GET` |
| Path | `/api/v1/claims/{claim_id}/trace` |
| Response | `ClaimTraceResponse { claim_id: str, count: int, events: List[TraceEvent] }` |
| Errors | None raised by the endpoint itself — an unknown `claim_id` returns `200` with `count: 0, events: []` (Phase 1 has no claims table to validate existence against) |

### Design decision: claim-scoped, not trace-scoped
`claim_id` is the identifier every caller already has — it's what a claim
submission and decision are keyed by. `trace_id` is an internal correlation
id minted per pipeline run that a frontend/ops user has no way to know in
advance. A parallel `GET /api/v1/traces/{trace_id}` endpoint would be
redundant for Phase 1 (one claim submission → one trace) and is deferred
until there's a concrete need — e.g. claim reprocessing producing more than
one trace per claim.

---

## PolicyRepository

**File**: `app/policy/policy_repository.py`

### Contract

| Property | Value |
|----------|-------|
| Purpose | Read-only data access to `policy_terms.json` — member roster, document requirements, minimum claim amount, submission deadline. **Not** a decision engine: it never evaluates coverage, waiting periods, exclusions, or co-pay. |
| Input | `policy_file_path` (constructor) — resolved via `app.config.paths.resolve_source_file`, which checks CWD, the project root, and the repo root in that order (see Decision 14 in `docs/AI_HANDOFF.md`) |
| Output | Typed accessors: `get_member(id) -> Optional[Member]`, `get_document_requirements(category) -> DocumentRequirement`, `has_category(category) -> bool`, `policy_id`, `minimum_claim_amount`, `submission_deadline_days` |
| Errors | `PolicyLoadError` if the file is missing or not valid JSON (raised at construction, not lazily) |
| Side effects | None — read-only |
| Failure behavior | Fails fast at startup (via the `get_policy_repository` FastAPI dependency), rather than partway through a claim |

### Known source-data quirks (worth knowing, not something to "fix" in the protected file)
- `opd_categories` keys are lowercase (`"consultation"`); `document_requirements` keys are uppercase (`"CONSULTATION"`). `has_category` matches case-insensitively.
- Several employees' `dependents` arrays reference member IDs (e.g. `DEP003`–`DEP006`) that have no corresponding entry in the `members` array. `get_member` correctly returns `None` for those.
- Dependents' own `join_date` is absent in the source JSON; `PolicyRepository` infers it from the primary member's `join_date` at load time (a two-pass load — primaries first, then dependents).

---

## ClaimValidationAgent

**File**: `app/agents/claim_validation_agent.py`

### Contract

| Property | Value |
|----------|-------|
| Purpose | The first pipeline stage — member existence, policy ID match, category structural validity, minimum claim amount. Nothing about coverage, waiting periods, or exclusions. |
| Input | `ClaimSubmission` (async `run(submission)`) |
| Output | `ValidationResult { valid: bool, errors: List[ValidationIssue], warnings: List[ValidationIssue] }` |
| Dependencies | `PolicyRepository` only — **no AI provider** (constructed with `ai_provider=None`; see `BaseAgent`'s Phase 2A update below) |
| Errors | **Never raises for an expected validation failure** — returns `valid=False` with structured `errors` instead. Only a genuine infrastructure problem (e.g. `PolicyRepository` itself failing) would propagate. |
| Failure behavior | N/A — purely deterministic, no external calls to fail |

### Deliberately not implemented (Phase 2A scope)
Submission-deadline checking (`LATE_SUBMISSION`) is not implemented — see Decision 15 in `docs/AI_HANDOFF.md` for why (the assignment's fixture dates would always appear "late" against the real system clock).

---

## DocumentVerificationAgent

**File**: `app/agents/document_verification_agent.py`

### Contract

| Property | Value |
|----------|-------|
| Purpose | Determine what document types were submitted, what's required (from `PolicyRepository`), what's missing/wrong/unreadable, and whether processing can continue. |
| Input | `run(*, claim_category, documents: List[DocumentMetadata], classifications: Optional[Dict[str, DocumentClassification]])` |
| Output | `DocumentVerificationResult { status, required_documents, received_documents, missing_documents, wrong_documents, quality_issues, classifications, user_message, confidence, ai_calls }` |
| Dependencies | `AIProvider` (via `BaseAgent`) + `PolicyRepository` + `DocumentStorage` (only used when a document carries a `storage_reference`) |
| AI interaction | For each document **without** a pre-supplied `DocumentClassification`: if it has a `storage_reference` (a real upload), `_classify_from_content()` reads the actual bytes via `DocumentStorage.read()` and calls `AIProvider.analyze_document()` — real multimodal classification from the document's actual visual content, never filename or declared type. If it has no `storage_reference` (an evaluation fixture with no `actual_type` supplied — rare, since fixtures normally pre-supply ground truth), `_classify_from_text_only()` calls `AIProvider.generate_structured()` using `build_document_classification_request()` as a documented fallback. Documents that already have a pre-supplied `DocumentClassification` skip the AI call entirely. Each real call's `AITraceMetadata` (provider, model, latency, token counts) is collected into `ai_calls`. Verified against real Gemini calls on real uploaded files — see `docs/AI_HANDOFF.md` "Real Document Upload Verification." |
| Errors | `ExtractionError` if the AI response can't be parsed into a valid `DocumentClassification` (invalid enum value, missing field) |
| Failure behavior | Raises on a genuine AI/parse failure — the caller (`ClaimsPipeline`) is responsible for catching this and degrading gracefully; the agent itself does not swallow errors or silently assume a document is valid |

### Status decision (`DocumentVerificationStatus`)
Priority order, highest first: any `UNREADABLE`/`PARTIAL` quality document → `NEEDS_RESUBMISSION`; else any required type with zero matching documents → `BLOCKED`; else `PASS` (an extraneous/`wrong_documents` entry alone, with all required types present, does not block).

### `user_message` generation
Built entirely from the structured result (`missing`, `wrong`, `quality_issues`, `received_documents` with counts) — never a hardcoded per-test-case string. Same code path produces TC001's and TC002's messages.

### Real vs. fallback AI classification
The primary path is real multimodal document understanding: `analyze_document()` is given the document's actual bytes (base64-encoded, wrapped in `ImageContent`/`DocumentContent`) and returns type/quality/patient-name/confidence extracted from what the model actually sees — verified end-to-end with real uploaded synthetic prescription/bill images and a real Gemini API key (see `docs/AI_HANDOFF.md`). The text-only `generate_structured()` path exists solely for evaluation fixtures that supply no `actual_type` and therefore have no bytes to analyze — it is not used for any real upload, since every real upload always has a `storage_reference`.

---

## CrossDocumentValidationAgent

**File**: `app/agents/cross_document_validation_agent.py`

### Contract

| Property | Value |
|----------|-------|
| Purpose | Cross-document consistency — Phase 2A implements patient-identity matching only (what TC003 needs), deliberately not a general validation engine. |
| Input | `run(classifications: List[DocumentClassification])` |
| Output | `CrossDocumentValidationResult { status: PASS \| BLOCKED, patient_names: Dict[str, str], user_message, confidence }` |
| Dependencies | None — no `AIProvider`, no `PolicyRepository`. Pure comparison over already-extracted names. |
| Matching | Case/whitespace-insensitive exact match (`_normalize_name`) — **not** fuzzy/typo-tolerant matching. Documented limitation, not a bug: "Rajesh Kumar" vs. "Rajesh  Kumar" matches; "Rajesh Kumar" vs. "Raj Kumar" does not. |
| Errors | None — always returns a result; fewer than two named documents is `PASS` (nothing to compare) |
| Failure behavior | N/A — no external calls |

---

## DocumentInputAdapter

**File**: `app/services/document_input_adapter.py`

### Contract

| Property | Value |
|----------|-------|
| Purpose | The single input boundary, with two entry points that both converge on the same output shape: `(ClaimSubmission, Dict[str, DocumentClassification])` — consumed identically by `ClaimsPipeline` regardless of which entry point produced it. |
| `to_domain(request: ClaimSubmissionRequest)` | The **fixture path** — evaluation test cases only. Converts ground-truth fields (`actual_type`/`quality`/`patient_name_on_doc`) into a pre-supplied classifications map, standing in for an AI classification so `test_cases.json` cases run deterministically without hitting a real AI provider. |
| `from_uploads(fields: ClaimSubmissionFields, uploads: List[UploadLike], document_storage: DocumentStorage)` | The **real-submission path** — reads each `UploadLike` (a `Protocol`, not `fastapi.UploadFile`, so this stays framework-agnostic), validates it via `validate_upload()`, saves it via `document_storage.save()`, and builds `DocumentMetadata` entries with a `storage_reference` set and **no** pre-supplied classification — forcing `DocumentVerificationAgent` down its real multimodal path. |
| Output | `ClaimSubmission` (domain) + a classifications map containing an entry **only** for documents that had ground truth supplied (fixtures) — always empty for real uploads |
| Supported sources | `app/api/v1/claims.py` (multipart `UploadFile`s via `from_uploads`); `app/evaluation/runner.py`'s `request_from_test_case()` (test_cases.json → `to_domain`) |
| Errors | `from_uploads` propagates `UnsupportedDocumentTypeError`/`EmptyDocumentError`/`DocumentTooLargeError` from `validate_upload()` — all recoverable `DocumentError` subclasses the API layer turns into 4xx responses, never a 500 |
| Guarantee | Neither entry point contains business rules (no validity/coverage logic) or per-test-case knowledge — `to_domain` does not know "TC001" exists, and `from_uploads` does not know what a valid claim looks like. Both entry points are the **only** place the real/fixture distinction exists; every downstream component (agents, `ClaimsPipeline`) is identical either way. |

---

## DocumentStorage

**File**: `app/storage/document_storage.py`

### Contract

| Property | Value |
|----------|-------|
| Purpose | Abstraction over where uploaded document bytes physically live — filesystem today, swappable to S3 (or similar) later without touching the pipeline, agents, or API layer. |
| Interface | `save(claim_id: str, filename: str, content: bytes) -> str` (returns a `storage_reference`), `read(storage_reference: str) -> bytes`, `delete(storage_reference: str) -> None` |
| Implementation | `LocalFileDocumentStorage(base_dir)` — writes to `{base_dir}/{claim_id}/{generated_filename}` |
| `generate_storage_filename()` | Produces `{uuid4}.{validated_extension}` — **never** the client's original filename, closing both path-traversal and filename-collision risk at the source |
| `validate_upload()` | Checks: file is non-empty, size ≤ `settings.max_upload_bytes`, extension is one of `.pdf`/`.jpg`/`.jpeg`/`.png`, and the file's **magic bytes** match a real PDF (`%PDF`)/JPEG (`\xff\xd8\xff`)/PNG (`\x89PNG\r\n\x1a\n`) signature — a spoofed `Content-Type` header alone cannot pass |
| Path safety | `LocalFileDocumentStorage._resolve()` does a `Path.resolve()` + containment check against `base_dir` as defense-in-depth, even though `storage_reference` is always server-generated, never client-supplied |
| Never exposed | `storage_reference` lives on the domain model (`DocumentMetadata.storage_reference`) and the ORM (`ClaimDocumentORM.storage_reference`) but is explicitly excluded from `ClaimDocumentSummary` (the API response schema) — no endpoint ever returns a filesystem path |
| Consumers | `app/api/v1/claims.py` (`save`, at upload time), `DocumentVerificationAgent._classify_from_content()` (`read`, for real multimodal classification) |

---

## ClaimsPipeline

**File**: `app/pipeline/pipeline.py`

### Contract

| Property | Value |
|----------|-------|
| Purpose | Orchestrates `ClaimValidationAgent` → `DocumentVerificationAgent` → `CrossDocumentValidationAgent`, stopping early on the first blocking outcome. Phase 2A only — no policy evaluation or decision generation. |
| Input | `run(claim: Claim, *, classifications, tracer: TraceService)` |
| Output | The same `Claim`, mutated in place: `status`, `stopped_at`, `user_message`, and whichever `*_result` fields the reached stages populated |
| Execution order | Claim Validation → Document Verification → Cross-Document Validation, each gated on the previous stage's result |
| Stopping conditions | `ValidationResult.valid == False` → stop after stage 1; `DocumentVerificationResult.status != PASS` → stop after stage 2; `CrossDocumentValidationResult.status != PASS` → stop after stage 3 |
| Trace behavior | One `TraceContext`/`TraceService` per claim, injected by the caller (never constructed internally). Each stage emits `STARTED` → `COMPLETED` (with metadata/confidence, and — for `DOCUMENT_VERIFICATION` only — `ai_metadata` from any real AI call made) or `FAILED`; unreached stages get `SKIPPED`; exactly one `PIPELINE`-component event summarises the run (`COMPLETED`/`WARNING`/`FAILED`) — see `docs/architecture.md` for the full rationale. |
| **Guarantee** | **Never raises.** A genuine stage failure (AI timeout, parse error) is caught per-stage, recorded as `FAILED` in the trace, and reflected back as a degraded `Claim` (`status=BLOCKED`, an explanatory `user_message`) rather than propagating — see `_degrade()`. This mirrors the assignment's graceful-failure requirement (verified live — see `docs/AI_HANDOFF.md`). |
| Failure behavior | Downstream stages after a failure get `SKIPPED`; the failing stage's result field stays `None` (never fabricated as if it had passed) |

---

## Claim API

**Files**: `app/api/v1/claims.py`, `app/api/v1/schemas.py`

| Endpoint | Contract |
|----------|----------|
| `POST /api/v1/claims` | **`multipart/form-data`** (not JSON): `member_id`/`policy_id`/`claim_category`/`treatment_date`/`claimed_amount`/`hospital_name`/`ytd_claims_amount` as `Form(...)` fields, plus one or more real files under the `documents` field as `File(...)`. Each file is validated (`validate_upload()`), stored (`DocumentStorage.save()`), then handed to `DocumentInputAdapter.from_uploads()`. Runs the full Phase 2A pipeline synchronously (including real multimodal classification) and returns `ClaimResponse` (201). A validation failure (bad file type, empty file, oversized file) returns a 4xx with a `DocumentError` message before any AI call is made. Never 500s on an AI/pipeline failure — see `ClaimsPipeline`'s guarantee above; a technical failure still comes back as a normal `ClaimResponse` with `status=BLOCKED`. |
| `GET /api/v1/claims/{claim_id}` | Returns `ClaimResponse`, or `404` if the claim_id is unknown. Never exposes raw SQLAlchemy rows or `storage_reference` — `ClaimRepository._to_domain` and `ClaimResponse.from_claim` are the only conversion points, and `ClaimDocumentSummary` deliberately excludes `storage_reference`. |

`ClaimResponse` never includes decision fields (`APPROVED`/`PARTIAL`/`REJECTED`/`MANUAL_REVIEW`) — those don't exist until a later phase.

There is no document-type field anywhere in the request — the member cannot declare what a document is; `DocumentVerificationAgent` determines it from the file's actual content.

---

## ClaimRepository

**File**: `app/repositories/claim_repository.py`

Unlike `TraceRepository` (deliberately not `BaseRepository` — see its own contract above), `ClaimRepository` genuinely is single-entity CRUD-by-id and implements `BaseRepository[Claim, str]` properly: `save(claim)` (upsert) and `get_by_id(id)`. Documents are a normalized child table (`claim_documents`); validation/verification results are stored as JSON columns on the `claims` row (same pattern as `TraceEventORM.metadata_json` — read as a whole with the claim, never queried field-by-field).

---

## Planned Phase 2B/3 Contracts (stubs)

These components will receive full contracts when implemented:

### PolicyEngine
- **Input**: `Claim` + loaded `policy_terms.json`
- **Output**: `PolicyEvaluationResult` (coverage, limits, waiting periods, exclusions)
- **Guarantee**: Purely deterministic; no AI calls

### FinancialCalculationService
- **Input**: `Claim` + `PolicyEvaluationResult`
- **Output**: `FinancialBreakdown`
- **Guarantee**: Purely deterministic; no AI calls; exact Decimal arithmetic

### DocumentExtractionAgent
- **Input**: `Document` + `AIProvider`
- **Output**: `ExtractedDocumentData` with confidence score
- **Guarantee**: Never raises on partial extraction; flags unextracted fields

### DecisionGenerationAgent
- **Input**: `Claim` + `PolicyEvaluationResult` + `FinancialBreakdown`
- **Output**: `ClaimDecision` (APPROVED/PARTIAL/REJECTED/MANUAL_REVIEW)
- **Guarantee**: Never raises; always returns a decision (may be `MANUAL_REVIEW` on upstream failures)
