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
| Purpose | Orchestrates `ClaimValidationAgent` → `DocumentVerificationAgent` → `CrossDocumentValidationAgent` → `DocumentExtractionAgent` (Phase 2B), stopping early on the first blocking outcome. Still no policy evaluation or decision generation. |
| Input | `run(claim: Claim, *, classifications, tracer: TraceService)` |
| Output | The same `Claim`, mutated in place: `status`, `stopped_at`, `user_message`, and whichever `*_result` fields the reached stages populated (including `extraction_result`, Phase 2B) |
| Execution order | Claim Validation → Document Verification → Cross-Document Validation → **Document Extraction**, each gated on the previous stage's result. Extraction runs last — after both early-stop checks already passed — because it's the most expensive stage (real per-document multimodal AI calls) and the least worth paying for on a claim that was going to stop anyway; see `docs/architecture.md` "Document Extraction". |
| Stopping conditions | `ValidationResult.valid == False` → stop after stage 1; `DocumentVerificationResult.status != PASS` → stop after stage 2; `CrossDocumentValidationResult.status != PASS` → stop after stage 3. Stage 4 (extraction) has no stopping condition of its own — a per-document extraction failure degrades `extraction_result.has_failures`/`confidence`/`user_message`, never blocks the claim (see `DocumentExtractionAgent`'s own never-raise guarantee above). |
| `document_extraction_agent` | **Optional**, defaults to `None`. When `None` (the evaluation runner; older tests predating Phase 2B), `DOCUMENT_EXTRACTION` is recorded `SKIPPED` — "no extraction agent configured" — not attempted, not a failure. Fully backward-compatible: no existing caller had to change. |
| Trace behavior | One `TraceContext`/`TraceService` per claim, injected by the caller (never constructed internally). Each stage emits `STARTED` → `COMPLETED` (with metadata/confidence, and — for `DOCUMENT_VERIFICATION`/`DOCUMENT_EXTRACTION` — `ai_metadata` from a real AI call made) or `FAILED`; unreached/unconfigured stages get `SKIPPED`; exactly one `PIPELINE`-component event summarises the run (`COMPLETED`/`WARNING`/`FAILED`) — see `docs/architecture.md` for the full rationale. |
| **Guarantee** | **Never raises.** A genuine stage failure (AI timeout, parse error) is caught per-stage, recorded as `FAILED` in the trace, and reflected back as a degraded `Claim` (`status=BLOCKED`, an explanatory `user_message`) rather than propagating — see `_degrade()`. This mirrors the assignment's graceful-failure requirement (verified live — see `docs/AI_HANDOFF.md`). |
| Failure behavior | Downstream stages after a failure get `SKIPPED`; the failing stage's result field stays `None` (never fabricated as if it had passed) |

---

## Claim API

**Files**: `app/api/v1/claims.py`, `app/api/v1/schemas.py`

| Endpoint | Contract |
|----------|----------|
| `POST /api/v1/claims` | **`multipart/form-data`** (not JSON): `member_id`/`policy_id`/`claim_category`/`treatment_date`/`claimed_amount`/`hospital_name`/`ytd_claims_amount` as `Form(...)` fields, plus one or more real files under the `documents` field as `File(...)`. Each file is validated (`validate_upload()`), stored (`DocumentStorage.save()`), then handed to `DocumentInputAdapter.from_uploads()`. Runs the full pipeline synchronously (classification, cross-document validation, and — Phase 2B — per-document extraction) and returns `ClaimResponse` (201). A validation failure (bad file type, empty file, oversized file) returns a 4xx with a `DocumentError` message before any AI call is made. Never 500s on an AI/pipeline failure — see `ClaimsPipeline`'s guarantee above; a technical failure still comes back as a normal `ClaimResponse` with `status=BLOCKED`, or (Phase 2B, per-document only) `status=PROCESSING` with `extraction_result.has_failures=True`. |
| `GET /api/v1/claims/{claim_id}` | Returns `ClaimResponse`, or `404` if the claim_id is unknown. Never exposes raw SQLAlchemy rows or `storage_reference` — `ClaimRepository._to_domain` and `ClaimResponse.from_claim` are the only conversion points, and `ClaimDocumentSummary` deliberately excludes `storage_reference`. |

`ClaimResponse` never includes decision fields (`APPROVED`/`PARTIAL`/`REJECTED`/`MANUAL_REVIEW`) — those don't exist until a later phase.

There is no document-type field anywhere in the request — the member cannot declare what a document is; `DocumentVerificationAgent` determines it from the file's actual content.

**Phase 2B additions**: `ClaimResponse.extraction_result: Optional[ClaimExtractionResult]` (the claim-level aggregate) and `ClaimDocumentSummary.extraction: Optional[DocumentExtractionResult]` (per-document, `None` if not extracted — check `extraction_result.skipped`/`.failures` for why). Both directly reuse the `app/domain/extraction.py` domain models — the same precedent `validation_result`/`document_verification_result`/`cross_document_validation_result` already set (Phase 2A domain results are the API contract, not a separately duplicated schema).

---

## ClaimRepository

**File**: `app/repositories/claim_repository.py`

Unlike `TraceRepository` (deliberately not `BaseRepository` — see its own contract above), `ClaimRepository` genuinely is single-entity CRUD-by-id and implements `BaseRepository[Claim, str]` properly: `save(claim)` (upsert) and `get_by_id(id)`. Documents are a normalized child table (`claim_documents`); validation/verification results are stored as JSON columns on the `claims` row (same pattern as `TraceEventORM.metadata_json` — read as a whole with the claim, never queried field-by-field).

### Extraction persistence (Phase 2B) — hybrid, not an opaque blob

| Layer | What's stored | Why |
|-------|---------------|-----|
| `ClaimORM.extraction_summary_json` | Claim-level rollup: `failures`, `skipped` (file_ids), `ai_calls`, `confidence`, `has_failures` | Small, read-as-a-whole-with-the-claim — same pattern as the other `*_result_json` columns. |
| `ClaimDocumentORM.extraction_json` | The **full** typed `DocumentExtractionResult` envelope (`model_dump(mode="json")`) for that document | The single source of truth for rehydration — `DocumentExtractionResult.model_validate(row.extraction_json)` resolves the correct one of the six extraction types via the discriminated union, no manual type dispatch needed on read. |
| `ClaimDocumentORM.diagnosis` / `.treatment` / `.document_date` / `.doctor_name` / `.total_amount` | Denormalised **projections** of `extraction_json`, computed at save-time via `_extraction_projection()` (uses `getattr` since the six schemas don't share field names for "the doctor" or "the headline amount") | Genuinely queryable columns for a future SQL query or `PolicyEngine` lookup, without needing to parse JSON — never a second source of truth, always derived from the same envelope also stored in `extraction_json`. |
| `ClaimDocumentORM.extraction_status` | `NOT_ATTEMPTED` \| `EXTRACTED` \| `FAILED` \| `SKIPPED` | Per-document extraction outcome, mirroring `processing_status`'s pattern for classification. |

No migration tooling exists yet (`Base.metadata.create_all()` only creates missing tables — see Known Issue 13); the seven new `ClaimDocumentORM` columns and `ClaimORM.extraction_summary_json` required deleting the gitignored local `data/claims.db` once, same as every previous phase's schema change.

---

## Extraction Domain Models (Phase 2B)

**File**: `app/domain/extraction.py`

### Contract

| Property | Value |
|----------|-------|
| Purpose | Typed, per-document-type structured extraction results — pure Pydantic, no database/FastAPI/AI SDK imports, same rules as `app/domain/models.py`. |
| Schemas | `PrescriptionExtraction`, `HospitalBillExtraction`, `LabReportExtraction`, `PharmacyBillExtraction`, `DentalReportExtraction`, `DischargeSummaryExtraction` — one per supported document type, all extending a shared `ExtractionBase` (`confidence: float` [required, AI-supplied, never fabricated], `warnings: List[str]`, `evidence: List[EvidenceItem]`). |
| Discriminated union | `ExtractionPayload = Annotated[Union[...6 schemas...], Field(discriminator="document_type")]` — each schema's `document_type` field is a fixed `Literal`, so `DocumentExtractionResult.model_validate(...)` (and its persistence round-trip) resolves the correct concrete type automatically from the stored JSON, no manual branching needed. |
| Money | Every amount field is `Optional[Decimal]`, parsed from an AI-supplied *string* (never a JSON float) via a `field_validator(mode="before")` that strips currency symbols/commas — see "Sentinel values" below. Never `float`. |
| Sentinel values | The AI-facing JSON schemas (`app/ai/prompts/*_extraction.py`) never use `null` — Gemini's `response_schema` doesn't reliably support nullable unions (Decision 8). "Not visible" is `""` for strings/dates/amounts, `[]` for lists, `"UNCLEAR"` for the tri-state signature/stamp/abnormal-flag fields. `app/domain/extraction.py`'s validators (`_empty_to_none`, `_to_decimal`, `_to_tristate_bool`) convert these into real `None`/`Decimal`/`bool` — the AI-facing shape and the domain shape are deliberately different. |
| Envelope | `DocumentExtractionResult { file_id, document_type, quality, patient: PatientInfo, document_date, source: "ai", extraction: ExtractionPayload }` — one per successfully extracted document. `.confidence`/`.warnings` are properties delegating to `.extraction` (not duplicated fields), so they persist as part of the same JSON blob. |
| Aggregate | `ClaimExtractionResult { extractions, failures: List[DocumentExtractionFailure], skipped: List[file_id], confidence, has_failures, ai_calls }` — one per claim, mirrors `DocumentVerificationResult`'s shape. |
| Errors | None raised by the domain models themselves — a schema violation raises Pydantic's `ValidationError`, which `DocumentExtractionAgent` catches and turns into a `DocumentExtractionFailure` (see below), never propagated raw. |

---

## DocumentExtractionAgent (Phase 2B)

**File**: `app/agents/document_extraction_agent.py`

### Contract

| Property | Value |
|----------|-------|
| Purpose | Answers "what does this document actually say?" — structured extraction beyond the classification `DocumentVerificationAgent` already does (type/quality/patient-name/confidence). Deliberately a separate agent, not a bigger `DocumentVerificationAgent` — see docs/architecture.md "Document Extraction" for the full separation-of-responsibilities rationale. |
| Input | `run(*, documents: List[DocumentMetadata])` — every document on the claim, already classified by `DocumentVerificationAgent` (`detected_type`/`quality`/`storage_reference` populated). |
| Output | `ClaimExtractionResult` (see above) |
| Dependencies | `AIProvider` (via `BaseAgent`) + `DocumentStorage` (to read real bytes) |
| AI interaction | **Always real** — `AIProvider.analyze_document()`, the same multimodal capability `DocumentVerificationAgent._classify_from_content()` uses. There is deliberately **no fixture/text-only extraction path** the way document verification has one for evaluation fixtures (Decision 17) — a document with no stored bytes cannot be extracted at all and is recorded as a per-document failure, not silently skipped as if it succeeded. Fixtures/mocks are only used at the `AIProvider` boundary in automated unit/integration tests, never as a second production code path. |
| Schema/prompt selection | One request builder + one Pydantic class per document type (`app/ai/prompts/{prescription,hospital_bill,lab_report,pharmacy_bill,dental,discharge_summary}_extraction.py`), dispatched by a `Dict[DocumentType, ...]` lookup keyed off the document's already-classified type — never a single generic schema. `DIAGNOSTIC_REPORT`/`PRE_AUTH_LETTER`/`UNKNOWN` have no schema and are recorded in `skipped`, not attempted. |
| Failure isolation | **Per-document, not per-claim.** Each document's extraction runs inside its own `try/except Exception` — an AI timeout, rate limit, malformed response, or missing `storage_reference` for one document becomes a `DocumentExtractionFailure` and the loop continues to the next document. `run()` itself **never raises** for a per-document problem, mirroring `ClaimsPipeline.run()`'s own never-raise guarantee (Decision 18) but scoped to one stage. Verified live against a real (organic) Gemini rate-limit error mid-claim — see `docs/AI_HANDOFF.md` "Real AI Verification". |
| Confidence | `ClaimExtractionResult.confidence` = min AI-reported confidence across successful extractions only (`None` if zero succeeded) — same "never fabricate, minimum wins" rule as `DocumentVerificationResult.confidence`. |
| Trace behavior | One `DOCUMENT_EXTRACTION` `STARTED`→`COMPLETED` pair per claim (via `ClaimsPipeline._run_stage`), **always `COMPLETED`, never `FAILED`, even with per-document failures** — the agent did its job (attempted every extractable document, recorded what it could and couldn't do); `FAILED` is reserved for a genuine failure of the agent itself, which the per-document isolation makes essentially unreachable in practice. Metadata: `{"documents_extracted", "failures", "skipped", "has_failures"}`; `ai_metadata` carries the first successful call's provider/model/latency. |

---

## PolicyEngine (Phase 2C)

**File**: `app/policy/policy_engine.py`

### Contract

| Property | Value |
|----------|-------|
| Purpose | The deterministic authority on "what does the policy say about this claim?" — coverage, limits, waiting periods, exclusions, pre-authorization, network status. Never decides the payable amount (`FinancialCalculationService`) or the final decision (`DecisionGenerationAgent`, Phase 2D). |
| Input | `evaluate(claim: Claim) -> PolicyEvaluationResult` — reads `claim.submission`, `claim.member` (Phase 2A's identity fix — see Decision 31), and `claim.extraction_result` (Phase 2B). Makes no AI calls (`ai_provider=None`, same pattern as `ClaimValidationAgent`). |
| Output | `PolicyEvaluationResult` (`app/domain/policy_evaluation.py`) — `covered`, `coverage_category`, a full `findings: List[PolicyRuleFinding]` (one per rule checked, `PASSED`/`FAILED`/`WARNING`/`NOT_APPLICABLE`), `passed_rules`/`failed_rules`/`warnings` (rule-code lists derived from `findings`, for cheap API/UI filtering without re-scanning), `requires_pre_authorization`/`pre_authorization_provided`, `waiting_period_applies`, `exclusion_applies`, `is_network_hospital: Optional[bool]` (`None` = hospital name unknown, not "not network"), `sub_limit`/`per_claim_limit`/`annual_opd_limit`/`copay_percent`/`network_discount_percent` (resolved values, for `FinancialCalculationService` to consume without re-reading `PolicyRepository`), `line_item_findings` (DENTAL/VISION only — per-line-item excluded/included verdicts), `confidence`. |
| Rule categories checked | Policy validity (id/dates/renewal), category coverage, submission deadline, minimum claim amount, per-claim limit, annual OPD limit, category sub-limit, copay (informational), network discount/hospital match, waiting periods (initial + pre-existing + specific-condition), exclusions (general + dental/vision-specific + per-line-item), pre-authorization (threshold + high-value-test list + provided-or-not), prescription requirement, dental-report requirement, registered-practitioner requirement, session limit (category-specific, DENTAL/alternative medicine). Each check is a private `_check_*` method returning one or more `PolicyRuleFinding`s — see the file for the full list; nothing here is hardcoded, every threshold/list comes from `PolicyRepository`. |
| Text matching | Diagnosis/treatment/procedure text (from `claim.extraction_result`) is matched against policy phrases via `_word_boundary_contains()` (regex `\b`-delimited, whole-word/whole-phrase) for specific-condition and general-exclusion keywords — **not** naive `phrase in text` substring containment, which produced a real false positive (`"hernia"` matching inside `"Herniation"`, an unrelated spinal-disc condition) found during live verification. See `docs/tradeoffs.md` "Diagnosis/Exclusion Normalization" for the full rationale, the hand-curated `_EXCLUSION_KEYWORDS`/`_CONDITION_ALIASES` tables, and why dental/vision line-item matching (`_match_short_phrases`) deliberately keeps bidirectional plain substring matching instead (short, closed-vocabulary procedure names, not free-text diagnoses). |
| Failure handling | `PolicyEngine.evaluate()` can raise (e.g. malformed extraction data) — `ClaimsPipeline` runs it through `_run_soft_stage()` (see below), which catches any exception, records `FAILED` in the trace, leaves `claim.policy_evaluation_result = None`, and **does not** treat this as an approval or a rejection — it's simply "policy could not be evaluated," surfaced to a human. `FinancialCalculationService` is skipped entirely if this happens (it has nothing to calculate from). |
| Trace behavior | One `POLICY_ENGINE` `STARTED`→`COMPLETED`/`FAILED` pair per claim. Metadata summarises `covered`/`failed_rules`/`waiting_period_applies`/`exclusion_applies` — never the full findings list verbatim (kept in the persisted result, not duplicated into trace rows, same reasoning as extraction). |

---

## FinancialCalculationService (Phase 2C)

**File**: `app/services/financial_calculation_service.py`

### Contract

| Property | Value |
|----------|-------|
| Purpose | Answers "what is the mathematically eligible/payable amount, given the policy findings?" — pure `Decimal` arithmetic, no AI, no policy-rule knowledge of its own (it consumes `PolicyEvaluationResult`, it doesn't re-derive limits from `PolicyRepository`). |
| Input | `calculate(claim: Claim, policy: PolicyEvaluationResult) -> FinancialBreakdown` — **synchronous** (no I/O; wrapped in an `async` helper at the pipeline call site only because `_run_stage`'s signature expects an awaitable). |
| Output | `FinancialBreakdown` (`app/domain/models.py`, extended in Phase 2C) — `claimed_amount`, `eligible_amount`, `network_discount_percent`/`_amount`, `amount_after_network_discount`, `sub_limit`/`sub_limit_applied`, `per_claim_limit`/`per_claim_limit_applied`, `annual_opd_limit`/`annual_limit_applied`, `amount_after_limits`, `copay_percent`/`_amount`, `payable_amount`, `currency` ("INR"), `calculation_steps: List[str]` (human-readable audit trail, one entry per step actually applied), `warnings: List[str]` (reconciliation mismatches — see below), `confidence` (1.0, or 0.7 if any warning was recorded). |
| Calculation order | claimed/itemized-eligible amount → network discount → category sub-limit cap → per-claim limit cap → remaining annual OPD allowance cap (`max(annual_opd_limit - ytd_claims_amount, 0)`) → copay deduction → `payable_amount`. Fixed order, not configurable per claim — see `docs/tradeoffs.md` "Financial Calculation Order" for why this exact order was chosen and the one place it disagrees with `test_cases.json`'s own worked examples (TC006, TC010). |
| Eligible-amount resolution | DENTAL/VISION claims with `policy.line_item_findings` populated: sum of the *non-excluded* line items only (so "root canal covered, whitening excluded" produces a correctly-reduced base before any limit/copay math runs). Every other category: `claimed_amount` as submitted — **never** silently replaced by an AI-extracted bill total; a mismatch is reported as a `warnings` entry, the original submitted/extracted values are always preserved unchanged. |
| Rounding | `Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` at every intermediate money-producing step (discount amount, each cap comparison keeps full precision, copay amount) — never truncation, never Python's banker's-rounding default. See `docs/tradeoffs.md` "Rounding" for a worked example (`333.33 × 33% = 109.9989 → 110.00`, not `109.99`). |
| Bill amount reconciliation | `_check_reconciliation()` compares (a) sum(line items) vs. the document's own reported total, and (b) the reported total vs. the resolved eligible-amount base — a difference beyond a small ₹1.00 tolerance becomes a `warnings` entry with both values named, never an automatic correction of either. See `docs/tradeoffs.md` "Bill Amount Reconciliation". |
| Failure handling | Can raise (e.g. missing required policy fields). `ClaimsPipeline` runs it through `_run_soft_stage()`; a failure leaves `claim.financial_calculation_result = None` — **never** a guessed/defaulted payable amount. Also skipped (not attempted, not a failure) if `policy_evaluation_result` is `None`. |
| Trace behavior | One `FINANCIAL_CALCULATION` `STARTED`→`COMPLETED`/`FAILED`/`SKIPPED` pair. Metadata: `payable_amount`, `sub_limit_applied`/`per_claim_limit_applied`/`annual_limit_applied`, `warnings_count` — the full `calculation_steps` audit trail lives on the persisted result, not duplicated into the trace row. |

---

## FraudAnalysisAgent (Phase 2C)

**File**: `app/agents/fraud_analysis_agent.py`

### Contract

| Property | Value |
|----------|-------|
| Purpose | Answers "are there fraud/manual-review signals for this claim?" using deterministic thresholds from `policy_terms.json`'s `fraud_thresholds` (via `PolicyRepository`) — never itself an approval/rejection decision, only flags for `DecisionGenerationAgent` (Phase 2D) to weigh later. |
| Input | `run(claim: Claim) -> FraudAnalysisResult`. Runs independently of `PolicyEngine`/`FinancialCalculationService` — a policy or financial failure does **not** skip fraud analysis, since fraud signals (same-day pattern, high value) are meaningful regardless of whether coverage/payable amount could be computed. |
| Output | `FraudAnalysisResult` (`app/domain/fraud.py`) — `risk_level` (`LOW`/`MEDIUM`/`HIGH`), `flags: List[FraudFlag]` (`code`/`message`/`evidence`, one per triggered threshold), `deterministic_thresholds_triggered: List[str]` (flag codes, for cheap filtering), `same_day_claim_count`/`monthly_claim_count` (**current claim always included** — a claim with zero prior history still counts as 1, a documented assumption), `is_high_value`, `requires_manual_review`, `ai_risk_score: Optional[float]` (**always `None` in this phase** — deliberately reserved, never populated from a deterministic signal, see below), `confidence`. |
| Thresholds | All four read from `PolicyRepository.fraud_thresholds`, never hardcoded: `same_day_claims_limit` (strictly-greater-than triggers `SAME_DAY_CLAIMS_LIMIT_EXCEEDED`), `monthly_claims_limit` (`MONTHLY_CLAIMS_LIMIT_EXCEEDED`), `high_value_claim_threshold` (`>=` triggers `HIGH_VALUE_CLAIM`, `MEDIUM` risk on its own), `auto_manual_review_above` (strictly-greater-than triggers `AUTO_MANUAL_REVIEW_THRESHOLD_EXCEEDED`, forces `HIGH` risk + `requires_manual_review=True`). `requires_manual_review` is **not** set by `is_high_value` alone — see `docs/tradeoffs.md` "Fraud Counting Semantics". |
| Historical claims | `_resolve_history()` — same "fixture ground truth vs. real query" pattern as `DocumentInputAdapter` (Decision 17): `submission.claims_history` (evaluation-runner/test-fixture path, e.g. TC009) takes priority when present; otherwise queries the real persisted history via `ClaimRepository.list_by_member()` (excluding the current claim by id). Never fabricates history — no repository and no fixture data means an empty list, not an invented one. |
| AI/deterministic separation | This phase makes **zero AI calls** (`ai_provider=None`) — every threshold is exact and policy-defined, so a deterministic-only implementation is sufficient and more explainable than an AI-assisted score would be for this phase. `ai_risk_score` exists on the result model specifically so a *future* AI-assisted signal has a field to populate **without ever being conflated with** `deterministic_thresholds_triggered` — the two must never merge into one undifferentiated "risk score". |
| Failure handling | Raises `ComponentFailureError` (recoverable) when `submission.simulate_component_failure=True` — wires up a Phase 0 domain field that was unused until this phase. `ClaimsPipeline` runs fraud analysis through `_run_soft_stage()`; a failure leaves `claim.fraud_analysis_result = None`, never crashes the claim. |
| Trace behavior | One `FRAUD_ANALYSIS` `STARTED`→`COMPLETED`/`FAILED` pair. Metadata: `risk_level`, `flags_count`, `requires_manual_review`, `same_day_claim_count`/`monthly_claim_count`. |

---

## ClaimsPipeline — Phase 2C stages (Policy → Financial → Fraud)

**File**: `app/pipeline/pipeline.py`

### Contract additions

| Property | Value |
|----------|-------|
| New stages | Stage 5 `POLICY_ENGINE`, Stage 6 `FINANCIAL_CALCULATION`, Stage 7 `FRAUD_ANALYSIS` — all after `DOCUMENT_EXTRACTION`, all constructor arguments `Optional[...] = None` (same backward-compatibility trick as Decision 30 for `document_extraction_agent`; the evaluation runner and every pre-2C pipeline test needed zero changes). |
| Soft-fail, not hard-stop | Unlike the three Phase 2A stages (`ClaimValidationAgent`/`DocumentVerificationAgent`/`CrossDocumentValidationAgent`, which early-stop the claim with `status=BLOCKED` on a business-rule failure), Policy/Financial/Fraud are **soft-fail**: `_run_soft_stage()` (new helper, mirrors `_run_stage` but catches any exception and returns `(None, degraded=True)` instead of re-raising) — a failure records `FAILED` in the trace and leaves the corresponding `claim.*_result` field `None`, but `claim.status` stays `PROCESSING` and the pipeline continues to the next stage. This matches the assignment's explicit instruction not to implement final decision generation yet — these stages inform a future decision, they don't gate the claim themselves. |
| Stage skip conditions | `POLICY_ENGINE`: skipped if `policy_engine is None`. `FINANCIAL_CALCULATION`: skipped if `financial_calculation_service is None` **or** `claim.policy_evaluation_result is None` (distinct skip messages — "not configured" vs. "policy could not be evaluated" — so the trace/UI can tell the two apart). `FRAUD_ANALYSIS`: skipped only if `fraud_analysis_agent is None` — runs independently of Policy/Financial outcome (a policy or financial failure never skips fraud analysis). |
| Downstream-skip completeness | `_PIPELINE_ORDER`/`_DOWNSTREAM_OF` (module-level, `Dict[TraceComponent, List[TraceComponent]]`) — when a Phase 2A stage early-stops the claim (invalid claim / document verification blocked / cross-document validation blocked), **every** downstream stage (including all three Phase 2C stages) is explicitly recorded `SKIPPED` in the trace, not silently absent. Found via a failing regression test (`TestPhase2AFixStillEarlyStopsBeforePhase2C`) after adding the 3 new stages — the pre-existing early-stop blocks only skipped the single immediately-next stage, which was correct before Phase 2C but became incomplete once 3 more stages existed downstream. The same `_DOWNSTREAM_OF` mapping is also used by `_degrade()`'s exception-path skip list, replacing a previously-hardcoded list. |
| Persistence | `ClaimORM.policy_evaluation_result_json`/`financial_calculation_result_json`/`fraud_analysis_result_json` — simple JSON-blob columns (claim-level, not per-document, so no hybrid/denormalised-column treatment the way Phase 2B's extraction needed) on the existing `claims` row, same pattern as the Phase 2A `*_result_json` columns. `ClaimRepository.save()`/`_to_domain()` extended to persist/rehydrate all three. Verified to survive a database round-trip via a dedicated regression test (`test_policy_financial_fraud_results_survive_a_database_round_trip`, `tests/integration/test_claims_api.py`) — POST then a *separate* GET (a fresh `ClaimRepository.get_by_id()` call) confirms the results were actually written to SQLite and correctly rehydrated, not just present on the in-memory `Claim` the pipeline just produced. |

---
