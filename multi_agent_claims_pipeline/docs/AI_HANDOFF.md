# AI Handoff Document — Health Insurance Claims Processing System

> **CRITICAL**: This document must be updated at the end of EVERY phase.
> Future AI agents should read this document FIRST before making any changes.

---

## Project Objective

Build a production-quality health insurance claims processing system for the Plum AI Engineer Assignment.

The system evaluates OPD health insurance claims using a multi-agent AI pipeline, making decisions of:
`APPROVED` | `PARTIAL` | `REJECTED` | `MANUAL_REVIEW`

---

## Current Phase

**Phase 2B — Document Extraction & Structured Medical Data** ✅ COMPLETE
(Phase 0 — Foundation & Architecture ✅ COMPLETE, Phase 1 — Observability &
Trace Infrastructure ✅ COMPLETE, Phase 2A — Claim Foundation & Early
Document Verification, including the Real Document Upload correction, ✅
COMPLETE — history preserved below)

> **Phase 2B in one sentence**: a fourth pipeline stage,
> `DocumentExtractionAgent`, runs after cross-document validation and
> answers "what does this document actually say?" — patient/doctor
> identity, diagnosis, medications, line items, amounts, dates — using six
> document-type-specific Pydantic schemas and real Gemini multimodal calls,
> with per-document failure isolation so one bad document never blocks a
> claim. Explicitly does **not** implement policy evaluation, coverage,
> financial calculation, fraud analysis, or any final decision — see
> "Deliberately not implemented" under the Phase 2B summary below.

> **A Phase 2A gap was found and fixed while testing Phase 2B**:
> `CrossDocumentValidationAgent` validated documents only against each
> other, never against the claim's actual member — two internally-
> consistent wrong documents (e.g. both "Vikram Joshi" on a claim for
> member Rajesh Kumar) incorrectly passed. Fixed by propagating the
> already-resolved `Member` from `ClaimValidationAgent` through to
> `CrossDocumentValidationAgent`, which now checks document ↔ document
> (unchanged) and document ↔ member (new). See "Phase 2A identity-
> validation gap fixed" below for the full account.

> **Phase 2A required a correction after its first pass**: the initial
> implementation's UI only let a member *select a document type* from a
> dropdown — it never accepted an actual file. That does not satisfy the
> assignment ("the application MUST support real PDF/JPG/PNG document
> uploads"), and it meant `DocumentVerificationAgent` was classifying
> documents from a user-declared type/filename, not from real content. The
> correction (this section) replaced the dropdown with a real file-upload
> UI, added a `DocumentStorage` abstraction, switched `POST /api/v1/claims`
> to `multipart/form-data`, and made `DocumentVerificationAgent` classify
> from actual uploaded bytes via `AIProvider.analyze_document()`
> (multimodal), never from filename or user selection. See "Real Document
> Upload Correction" below for the full account.

---

## Current Architecture

### Backend Stack
- Python 3.11 + FastAPI 0.115.5 + Pydantic 2.x
- Async SQLAlchemy 2.0 + SQLite (dev) / PostgreSQL-ready
- pytest + pytest-asyncio for testing
- Uvicorn ASGI server

### Frontend Stack
- React 18 + TypeScript + Vite
- React Router v6
- No CSS framework (Vanilla CSS / inline styles)

### AI Stack
- **Google Gemini (current default provider, `AI_PROVIDER=gemini`)** — switched from Anthropic on 2026-08-09 at the user's request. Uses the `google-genai` SDK.
- Anthropic Claude remains fully implemented as an alternate provider (`AI_PROVIDER=anthropic`) — both adapters are maintained side by side to prove out the abstraction.
- `AIProvider` ABC ensures vendor agnosticism
- ONLY `app/ai/providers/anthropic_provider.py` imports the Anthropic SDK; ONLY `app/ai/providers/gemini_provider.py` imports `google-genai`

### Tracing / Observability Stack (Phase 1)
- `app/domain/trace.py` — vendor/storage-agnostic Pydantic models: `TraceEvent`, `TraceContext`, `TraceComponent`, `TraceEventType`, `TraceErrorInfo`, `AITraceMetadata`
- `app/tracing/service.py` — `TraceService` (the injected façade every future agent uses), `span()` async context manager, `redact_metadata()`, `create_trace_service()` factory
- `app/repositories/trace_models.py` + `trace_repository.py` — SQLAlchemy persistence (`trace_events` table) behind `TraceRepository`
- `app/api/v1/traces.py` — `GET /api/v1/claims/{claim_id}/trace`
- Full contract in `docs/component-contracts.md`; design rationale in `docs/architecture.md` §6

### Claim Foundation Stack (Phase 2A)
- `app/policy/policy_repository.py` — `PolicyRepository`: read-only access to `policy_terms.json` (members, document requirements, minimum claim amount) — **not** a decision engine
- `app/domain/verification.py` — `ValidationResult`, `DocumentVerificationResult`, `CrossDocumentValidationResult`, `DocumentClassification` and their status enums
- `app/agents/claim_validation_agent.py`, `document_verification_agent.py`, `cross_document_validation_agent.py` — the three Phase 2A pipeline stages
- `app/ai/prompts/document_verification.py` — `build_document_analysis_request()` (real multimodal classification from uploaded bytes, primary path) and `build_document_classification_request()` (text-only fallback, evaluation fixtures / documents with no stored bytes)
- `app/storage/document_storage.py` — `DocumentStorage` ABC + `LocalFileDocumentStorage` (real-upload correction; see below)
- `app/services/document_input_adapter.py` — the shared real-submission/evaluation-fixture input boundary: `to_domain()` (fixtures) and `from_uploads()` (real `UploadFile`s)
- `app/pipeline/pipeline.py` — `ClaimsPipeline`: orchestrates the three stages, early-stops, degrades gracefully on AI/infra failure, never raises
- `app/repositories/claim_models.py` + `claim_repository.py` — SQLAlchemy persistence (`claims`, `claim_documents` tables) behind `ClaimRepository`
- `app/api/v1/claims.py` — `POST /api/v1/claims` (multipart/form-data), `GET /api/v1/claims/{claim_id}`
- `app/evaluation/runner.py` + `scripts/run_eval.py` — runs TC001-TC003 through the real pipeline and reports PASS/FAIL
- Full contracts in `docs/component-contracts.md`; design rationale in `docs/architecture.md`'s "Claim Processing Flow (Phase 2A)" and "Real Document Upload" sections

### Real Document Upload Correction (Phase 2A, post-hoc fix)
- `app/storage/document_storage.py` — `DocumentStorage` ABC (`save`/`read`/`delete`), `LocalFileDocumentStorage` (filesystem now, S3-swappable later), `validate_upload()` (magic-byte signature check + size/MIME/extension validation), `generate_storage_filename()` (UUID, never the client's filename)
- `app/api/v1/claims.py` — rewritten from a JSON body to `Form(...)`/`File(...)` multipart handling; files are read once, validated, handed to `DocumentStorage`, then to `DocumentInputAdapter.from_uploads()`
- `app/agents/document_verification_agent.py` — branches on whether a document has a `storage_reference`: real bytes → `AIProvider.analyze_document()` (multimodal); no bytes (fixtures) → `AIProvider.generate_structured()` (text-only, documented fallback)
- Frontend `ClaimSubmission.tsx` — real `<input type="file" accept=".pdf,.jpg,.jpeg,.png" multiple>` behind an "+ Add Document" button; shows filename/size/checkmark per file with a Remove control; no document-type selector anywhere
- Frontend `ClaimDetail.tsx` — new `DocumentCard`/"Documents" section shows the AI-determined Type/Quality/Patient/Confidence per document, sourced entirely from the API response
- `storage_reference` (the filesystem-relative path) is on the domain model and ORM but deliberately excluded from `ClaimDocumentSummary` (API schema) — never exposed over HTTP
- Verified end-to-end with real Gemini calls on real uploaded synthetic images (not just fixtures) — see "Real Document Upload Verification" below

### Document Extraction Stack (Phase 2B)
- `app/domain/extraction.py` — `PrescriptionExtraction`, `HospitalBillExtraction`, `LabReportExtraction`, `PharmacyBillExtraction`, `DentalReportExtraction`, `DischargeSummaryExtraction` (all extending shared `ExtractionBase`: `confidence`/`warnings`/`evidence`), the `ExtractionPayload` discriminated union, `DocumentExtractionResult` (envelope), `DocumentExtractionFailure`, `ClaimExtractionResult`
- `app/ai/prompts/extraction_common.py` — shared "never hallucinate" rules text + request-builder helper reused by all six per-type prompt modules
- `app/ai/prompts/{prescription,hospital_bill,lab_report,pharmacy_bill,dental,discharge_summary}_extraction.py` — one modular prompt + AI-facing JSON schema per document type, each schema using sentinel values (`""`/`[]`/`"UNCLEAR"`) instead of `null` for the same Gemini-`response_schema`-subset reason Phase 2A's classification schema does (Decision 8)
- `app/agents/document_extraction_agent.py` — `DocumentExtractionAgent`: dispatches to the right prompt/schema per document's already-classified type, always calls `AIProvider.analyze_document()` (no fixture path — real AI only), isolates each document's failure so one bad document never blocks the others
- `app/pipeline/pipeline.py` — `ClaimsPipeline` gained a 4th, optional stage (`document_extraction_agent: Optional[...] = None`) after cross-document validation; `None` (evaluation runner, pre-2B tests) → `DOCUMENT_EXTRACTION` recorded `SKIPPED`, fully backward-compatible
- `app/repositories/claim_models.py` + `claim_repository.py` — `ClaimORM.extraction_summary_json` (claim-level rollup) + 7 new `ClaimDocumentORM` columns (`extraction_status`, `diagnosis`, `treatment`, `document_date`, `doctor_name`, `total_amount`, `extraction_json`) — hybrid persistence, not an opaque blob
- `app/api/v1/schemas.py` — `ClaimResponse.extraction_result` + `ClaimDocumentSummary.extraction`, directly reusing the `app/domain/extraction.py` domain models (same precedent as the Phase 2A `*_result` fields)
- Frontend `types/index.ts` — TypeScript mirror of the extraction domain models, including the discriminated `ExtractionPayload` union
- Frontend `ClaimDetail.tsx` — `DocumentCard` gained a collapsed-by-default "Extracted Information" toggle + `ExtractedInfo`, a document-type-specific operations-friendly renderer (never raw JSON)
- Full contracts in `docs/component-contracts.md`; design rationale in `docs/architecture.md`'s "Document Extraction (Phase 2B)" section

### Phase 2A identity-validation gap fixed (post-hoc fix, found during Phase 2B testing)

**The bug**: `CrossDocumentValidationAgent` validated documents *only*
against each other, never against the claim's actual member. Two
documents that agreed with each other always `PASS`ed regardless of who
they actually belonged to — e.g. member `EMP001` (Rajesh Kumar per
`policy_terms.json`) submitting two documents both saying "Vikram Joshi"
incorrectly passed cross-document validation, because the only comparison
being made was "do these two documents' patient names match each other,"
and they did.

**Why two internally-consistent wrong documents used to pass**: `ClaimValidationAgent`
already resolved the submitted `member_id` to a real `Member` (name
included) via `PolicyRepository.get_member()` — but only checked `is
None` (member exists?) and then **discarded** the resolved object. It was
never stored anywhere, never passed to any later stage.
`CrossDocumentValidationAgent.run(classifications)` had no parameter through
which member identity could even reach it — its signature made a
document-to-member comparison structurally impossible, not just unimplemented.
`Claim.member: Optional[Member]` existed on the domain model since Phase 0
but was never populated by anything, anywhere, in the whole pipeline.

**How member identity is now propagated**: `ValidationResult` (`app/domain/verification.py`)
gained a `member: Optional[Member]` field; `ClaimValidationAgent` now
returns the `Member` it already resolved instead of discarding it (no
second `PolicyRepository` lookup). `ClaimsPipeline.run()` copies it onto
`claim.member` right after Stage 1 (`claim.member = validation_result.member`)
— the same, previously-always-`None` field Phase 0 already reserved for
exactly this. Stage 3's call site now passes it through:
`self._cross_document_validation_agent.run(doc_result.classifications, member=claim.member)`.

**How document ↔ member validation works**: `CrossDocumentValidationAgent.run()`
gained an optional, keyword-only `member: Optional[Member] = None`
parameter (default `None` — every pre-fix caller, including the
evaluation runner, is unaffected). It now performs, in order:
1. **Document ↔ document** (unchanged, existing logic, checked first) — if
   the documents don't even agree with each other, that's still the
   reported mismatch, regardless of the member.
2. **Document ↔ claim member** (new) — once the documents agree with each
   other (or there's exactly one named document), their shared identity is
   compared against `member.name`, using the exact same `_normalize_name`
   case/whitespace-insensitive helper the document-to-document check
   already used — so `"Rajesh Kumar"` / `"rajesh kumar"` / `" Rajesh Kumar "`
   are still the same identity, and no fuzzy matching was introduced.

A member mismatch produces a specific, actionable message naming both
identities (e.g. *"The uploaded documents identify the patient as Vikram
Joshi, but this claim is for Rajesh Kumar (EMP001). Please upload
documents belonging to the covered member."*) — never a generic "patient
mismatch" string. It reuses the existing `CrossDocumentValidationStatus.BLOCKED`
status and the pipeline's existing early-stop path — no new status, no
pipeline redesign. `status=BLOCKED`, `stopped_at=CROSS_DOCUMENT_VALIDATION`,
exactly like the pre-existing document-to-document mismatch case.

**Domain vocabulary considered and deliberately not force-fit**:
`RejectionReason.PATIENT_NOT_MEMBER` (`app/domain/models.py`) is the
conceptually-correct label for this scenario, but Phase 2A has no
`ClaimDecision`/`rejection_reasons` field yet to attach it to (that's
Phase 3's job) — forcing it in now would mean adding a new field/schema
change purely to hold an enum value nothing reads yet, which is exactly
the kind of redesign this fix was scoped to avoid. `DocumentPatientMismatchError`
(`app/domain/errors.py`) was also considered and deliberately **not**
raised — consistent with Decision 19 (a correctly-detected `BLOCKED`
verdict is the agent succeeding at its job, not a failure; raising here
would incorrectly route through `ClaimsPipeline`'s `FAILED`/degrade path
instead of the normal early-stop path the existing document-to-document
mismatch already uses unmodified).

**Regression tests added** (17 new: 9 unit + 2 pipeline integration + 1 API
integration on top of new test infrastructure): `tests/unit/test_cross_document_validation_agent.py`
gained `TestOmittedMemberIsFullyBackwardCompatible` (1 test — no `member=`
passed still behaves exactly as before) and `TestDocumentMemberIdentityMismatch`
(8 tests: same-wrong-person-blocks, message names both identities
specifically, correct-member-passes, one-correct-one-wrong-blocks,
case/whitespace-normalization-passes, existing doc-to-doc mismatch
unaffected by a supplied member, a single mismatching document also
blocks, and a dependent member_id is checked against their own name, not
the primary member's). `tests/integration/test_claims_pipeline.py` gained
`TestCrossDocumentMemberIdentityMismatch` (2 tests: the full EMP001 +
"Vikram Joshi"×2 pipeline run reaches `BLOCKED`/`CROSS_DOCUMENT_VALIDATION`
with both names in the message and safe structured trace metadata, plus a
regression guard that matching documents still `PASS`). `tests/integration/test_claims_api.py`
gained `test_submit_claim_member_identity_mismatch_blocks` (the same
scenario over real HTTP, including asserting the trace endpoint shows the
`BLOCKED` `CROSS_DOCUMENT_VALIDATION` event with `expected_member_name` in
its metadata).

**Test results**: 298/298 backend tests pass (286 Phase-2B baseline + 12
new), 35/35 frontend tests pass (unchanged — no frontend files needed
modification, since `ClaimDetail.tsx` already renders any `BLOCKED`
claim's `user_message` generically), `npm run build` clean. TC001/TC002/TC003
re-run via `scripts/run_eval.py`: still 3/3 PASS, unaffected (TC001/TC002
never reach cross-document validation; TC003's existing document-to-document
mismatch is unchanged since check (a) still runs first). No Phase 2B files
(`app/domain/extraction.py`, `app/agents/document_extraction_agent.py`,
`app/ai/prompts/*_extraction.py`) were touched by this fix — Phase 2B's own
33 tests all continued passing untouched, confirming no regression.

---

## Implemented Components (Phase 0 + Phase 1 + Phase 2A + Phase 2B)

### Backend

| Component | File | Status |
|-----------|------|--------|
| Settings (Pydantic BaseSettings) | `app/config/settings.py` | ✅ |
| Domain models | `app/domain/models.py` | ✅ |
| Error hierarchy | `app/domain/errors.py` | ✅ |
| AIProvider ABC | `app/ai/providers/base.py` | ✅ |
| AI request/response schemas | `app/ai/schemas/ai_schemas.py` | ✅ |
| AnthropicProvider adapter | `app/ai/providers/anthropic_provider.py` | ✅ |
| GeminiProvider adapter (default) | `app/ai/providers/gemini_provider.py` | ✅ |
| Provider factory | `app/ai/providers/factory.py` | ✅ |
| BaseAgent | `app/agents/base_agent.py` | ✅ |
| FastAPI DI (deps.py) | `app/api/deps.py` | ✅ |
| Health endpoint | `app/api/v1/health.py` | ✅ |
| Structured logging | `app/tracing/logging.py` | ✅ |
| Database foundation | `app/repositories/database.py` | ✅ |
| Repository ABC | `app/repositories/base.py` | ✅ |
| FastAPI app factory | `app/main.py` | ✅ |
| PolicyEngine placeholder (coverage/waiting-period decisions) | `app/policy/policy_engine.py` | ⬜ stub |
| Trace domain models | `app/domain/trace.py` | ✅ |
| TraceService | `app/tracing/service.py` | ✅ |
| TraceEventORM | `app/repositories/trace_models.py` | ✅ |
| TraceRepository | `app/repositories/trace_repository.py` | ✅ |
| Trace API endpoint | `app/api/v1/traces.py` | ✅ |
| Trace Repository DI | `app/api/deps.py` (`TraceRepositoryDep`) | ✅ |
| PolicyRepository | `app/policy/policy_repository.py` | ✅ |
| Verification domain models | `app/domain/verification.py` | ✅ |
| ClaimValidationAgent | `app/agents/claim_validation_agent.py` | ✅ |
| DocumentVerificationAgent | `app/agents/document_verification_agent.py` | ✅ |
| CrossDocumentValidationAgent | `app/agents/cross_document_validation_agent.py` | ✅ |
| Document classification prompt | `app/ai/prompts/document_verification.py` | ✅ |
| DocumentInputAdapter | `app/services/document_input_adapter.py` | ✅ |
| ClaimsPipeline | `app/pipeline/pipeline.py` | ✅ |
| ClaimORM / ClaimDocumentORM | `app/repositories/claim_models.py` | ✅ |
| ClaimRepository | `app/repositories/claim_repository.py` | ✅ |
| Claims API (`POST`/`GET /claims`) | `app/api/v1/claims.py`, `schemas.py` | ✅ |
| Evaluation runner (TC001-TC003) | `app/evaluation/runner.py`, `scripts/run_eval.py` | ✅ |
| Source-file path resolution | `app/config/paths.py` | ✅ |
| DocumentStorage ABC + LocalFileDocumentStorage | `app/storage/document_storage.py` | ✅ |
| Multipart claims API (real file upload) | `app/api/v1/claims.py` | ✅ |
| Real multimodal document classification | `app/agents/document_verification_agent.py` (`_classify_from_content`) | ✅ |
| Extraction domain models (6 schemas + envelope) | `app/domain/extraction.py` | ✅ |
| Extraction prompts (6 document types + shared helpers) | `app/ai/prompts/*_extraction.py` | ✅ |
| DocumentExtractionAgent | `app/agents/document_extraction_agent.py` | ✅ |
| ClaimsPipeline Stage 4 (extraction, optional agent) | `app/pipeline/pipeline.py` | ✅ |
| Extraction persistence (hybrid: JSON + queryable columns) | `app/repositories/claim_models.py`, `claim_repository.py` | ✅ |
| Claim API extraction fields | `app/api/v1/schemas.py` | ✅ |

### Frontend

| Component | File | Status |
|-----------|------|--------|
| Shared TypeScript types | `src/types/index.ts` | ✅ |
| API service abstraction | `src/services/api.ts` | ✅ |
| useHealth hook | `src/hooks/useHealth.ts` | ✅ |
| App shell + routing | `src/App.tsx` | ✅ |
| Dashboard page | `src/pages/Dashboard.tsx` | ✅ |
| Vite config | `vite.config.ts` | ✅ |
| TraceViewer component (reusable) | `src/components/TraceViewer.tsx` | ✅ |
| useClaimTrace hook | `src/hooks/useClaimTrace.ts` | ✅ |
| ClaimSubmission page (real file upload UI) | `src/pages/ClaimSubmission.tsx` | ✅ |
| ClaimDetail page (mounts TraceViewer, document results, extraction) | `src/pages/ClaimDetail.tsx` | ✅ |

`TraceViewer`/`useClaimTrace` (built in Phase 1) are now mounted for the
first time, in `ClaimDetail.tsx` — unmodified except a display bug fixed
in passing (`MetadataChips` rendered object-valued metadata, e.g.
`patient_names`, as `"[object Object]"`; now `JSON.stringify`s it).

### Tests

| Test | File | Status |
|------|------|--------|
| Config loading | `tests/unit/test_config.py` | ✅ |
| Domain model validation | `tests/unit/test_domain_models.py` | ✅ |
| AI provider interface | `tests/unit/test_ai_provider.py` | ✅ |
| Health endpoint integration | `tests/integration/test_health.py` | ✅ |
| Trace domain models | `tests/unit/test_trace_domain.py` | ✅ |
| TraceService (span, redaction, errors) | `tests/unit/test_trace_service.py` | ✅ |
| TraceRepository persistence | `tests/integration/test_trace_persistence.py` | ✅ |
| Trace API endpoint | `tests/integration/test_trace_api.py` | ✅ |
| TraceViewer component (all 5 statuses + metadata formatting) | `frontend/src/components/TraceViewer.test.tsx` | ✅ (vitest, 16 tests) |
| PolicyRepository | `tests/unit/test_policy_repository.py` | ✅ |
| ClaimValidationAgent | `tests/unit/test_claim_validation_agent.py` | ✅ |
| DocumentVerificationAgent (incl. AI-classification path + malformed-response handling) | `tests/unit/test_document_verification_agent.py` | ✅ |
| CrossDocumentValidationAgent | `tests/unit/test_cross_document_validation_agent.py` | ✅ |
| DocumentInputAdapter | `tests/unit/test_document_input_adapter.py` | ✅ |
| Verification domain models | `tests/unit/test_verification_domain.py` | ✅ |
| ClaimsPipeline (early-stop, full pass, graceful degradation on AI failure) | `tests/integration/test_claims_pipeline.py` | ✅ |
| Claims API — multipart upload, validation, storage wiring | `tests/integration/test_claims_api.py` | ✅ (16 tests) |
| TC001-TC003 via the real evaluation runner | `tests/integration/test_eval_tc001_tc003.py` | ✅ |
| DocumentStorage (magic-byte validation, path safety, save/read/delete) | `tests/unit/test_document_storage.py` | ✅ (18 tests) |
| DocumentInputAdapter — `from_uploads()` real-upload path | `tests/unit/test_document_input_adapter.py` (`TestFromUploads`) | ✅ |
| DocumentVerificationAgent — real-content classification path | `tests/unit/test_document_verification_agent.py` (`TestRealContentClassificationPath`) | ✅ |
| ClaimSubmission — real file picker, multi-file, remove, validation, submit | `frontend/src/pages/ClaimSubmission.test.tsx` | ✅ (11 tests) |
| ClaimDetail — AI-determined document results render, storage_reference never leaks | `frontend/src/pages/ClaimDetail.test.tsx` | ✅ (3 tests) |
| Extraction domain models — all 6 schemas, sentinel parsing, discriminated union | `tests/unit/test_extraction_domain.py` | ✅ (17 tests) |
| DocumentExtractionAgent — success, per-document failure isolation, schema selection, skip unsupported types | `tests/unit/test_document_extraction_agent.py` | ✅ (12 tests) |
| ClaimsPipeline — extraction stage runs after cross-doc validation, one failure doesn't block the claim, unconfigured agent skips cleanly | `tests/integration/test_claims_pipeline.py` (`TestDocumentExtractionStage`) | ✅ (3 tests) |
| Claims API — extraction surfaces in the response, survives a DB round-trip | `tests/integration/test_claims_api.py` | ✅ (+1 dedicated persistence test) |
| ClaimDetail — Extracted Information toggle, document-specific rendering, warnings, failure reason | `frontend/src/pages/ClaimDetail.test.tsx` | ✅ (+3 tests) |

---

## Important Design Decisions

### Decision 1: AIProvider via ABC, not Protocol
Used ABC (not `typing.Protocol`) so future providers can use `super()` if needed, and to make missing method implementations obvious at import time.

### Decision 2: AnthropicProvider uses `tool_use` for structured output
Claude's `tool_use` feature guarantees JSON output matching a schema. This is more reliable than asking Claude to output JSON in the text field and parsing it.

### Decision 3: Settings singleton with `lru_cache`
`get_settings()` is cached to avoid re-reading `.env` on every request. Tests call `get_settings.cache_clear()` to reset.

### Decision 4: `recoverable` flag on all errors
Every `ClaimsSystemError` subclass carries `recoverable: bool`. The Phase 2 orchestrator uses this flag to decide whether to skip a failed agent (recoverable) or halt processing (non-recoverable); `TraceService.failed()` (Phase 1) preserves this same flag onto every FAILED trace event via `error_info_from_exception`.

### Decision 5: Decimal for all financial amounts
All monetary amounts use `decimal.Decimal` to avoid floating-point rounding issues. Never use `float` for money.

### Decision 6: Policy loaded from JSON, never hardcoded
`policy_terms.json` is the single source of truth. The PolicyEngine (Phase 2) will load and cache it. No policy rule appears as a literal constant anywhere in the code.

### Decision 7: Frontend API proxy via Vite
The Vite dev server proxies `/api/*` to `http://localhost:8000`. No CORS complexity during development.

### Decision 8: Gemini uses `response_mime_type=application/json` + `response_schema` for structured output
Mirrors the role Anthropic's `tool_use` plays for `AnthropicProvider` — Gemini's controlled-generation feature guarantees JSON that (mostly) conforms to the given schema. Note: Gemini's `response_schema` accepts an OpenAPI-3.0-like subset, not full JSON Schema — revisit this in Phase 2 if extraction schemas need `$ref`, `oneOf`, etc., which Gemini does not support.

### Decision 9: `.env` resolves from the project root regardless of CWD
`Settings.model_config.env_file` now points at both `.env` (relative to CWD) and an absolute path to `<project-root>/.env`, computed from `settings.py`'s own location. This fixes a real Phase-0 bug: the README told users to `cp .env.example ../.env` (i.e., project root), but pydantic-settings only ever looked in the CWD, so running `uvicorn` from `backend/` silently ignored that file and fell back to defaults. See `app/config/settings.py`.

### Decision 10: Tracing is infrastructure, injected — not a base-agent mixin
`TraceService` is a plain constructor-injected object (`TraceService(context, sink=...)`), not something `BaseAgent` wires up automatically. Phase 1 deliberately does not touch `base_agent.py`: no agents exist yet to prove the integration against, and guessing at the shape now risks locking in the wrong call pattern. Phase 2's `ClaimsPipeline` constructs one `TraceService` per claim and passes it into each agent explicitly — same dependency-injection discipline as `AIProvider`.

### Decision 11: `TraceRepository` does not implement `BaseRepository[T, ID]`
Trace events are a one-claim-to-many-events relationship, not single-entity CRUD-by-id — forcing `get_by_id`/`save`/`delete` onto it would produce an interface nobody would actually call that way. `TraceRepository` instead exposes `create_event` / `list_by_trace_id` / `list_by_claim_id`, matching the assignment's explicit ask. Documented as a deliberate deviation in `docs/component-contracts.md`, not an oversight.

### Decision 12: Trace persistence is synchronous-per-event, not batched
`TraceService._emit()` awaits `sink.record(event)` immediately for every event, rather than buffering and flushing in batches. At claims-pipeline scale (dozens of events per claim, not thousands/sec) the extra round-trips are cheap, and it means a crash mid-pipeline never loses events that were already emitted — the trace is complete up to the point of failure, which is exactly what "reconstruct why it failed" needs. Revisit only if profiling at 10x load shows this is a real bottleneck.

### Decision 13: Trace API is claim-scoped (`/claims/{claim_id}/trace`), not trace-scoped
See `docs/component-contracts.md` "Trace API" section and `docs/architecture.md` §6 for the full reasoning — `claim_id` is what every caller already has; `trace_id` is an internal correlation id with no separate lookup UI yet. A `/traces/{trace_id}` endpoint was deliberately not added (avoids two redundant read paths for the same Phase-1 data).

### Decision 14: Source-of-truth files actually live at the repo root, not the project root
`policy_terms.json`, `test_cases.json`, `sample_documents_guide.md`, and `assignment.md` are one level above `multi_agent_claims_pipeline/` — not inside it, contrary to the tree documented in Phase 0's README/AI_HANDOFF. `app/config/paths.py`'s `resolve_source_file()` checks the CWD, the project root, and the repo root (in that order) so `PolicyRepository` and the evaluation runner work regardless of where a process is launched from — generalises the same fix Decision 9 applied to `.env`.

### Decision 15: Submission-deadline validation (`LATE_SUBMISSION`) is deliberately not implemented in Phase 2A
The assignment's own instructions for this phase only ask for treatment-date *validity* (already covered by `ClaimSubmission`'s existing Pydantic "not in the future" check), not a 30-day submission-deadline check. That's fortunate, because implementing one naively (`(date.today() - treatment_date).days > 30`) would make every supplied test case fail claim validation — the fixtures are dated 2024, evaluated against a system clock in 2026. Revisit once claims carry their own `submitted_at` timestamp independent of wall-clock "now".

### Decision 16: `BaseAgent.ai_provider` becomes optional
`ClaimValidationAgent` and `CrossDocumentValidationAgent` are purely deterministic — forcing them to accept (and never use) an `AIProvider` would be a fake dependency. `BaseAgent.__init__` now accepts `ai_provider: Optional[AIProvider] = None`; the `.ai_provider` property raises `AIProviderNotConfiguredError` if accessed without one, and `.has_ai_provider` lets a subclass check first. Backward-compatible — every existing caller that passes a real provider is unaffected.

### Decision 17: Real-vs-fixture input is one boundary (`DocumentInputAdapter`), not two pipelines
The assignment is explicit that the fixture adapter must not be "a fake AI implementation" and must stay outside business agents. `ClaimSubmissionRequest`/`ClaimDocumentInput` (in `app/services/document_input_adapter.py`) accept both a real submission's fields (`declared_type`) and an evaluation fixture's ground truth (`actual_type`/`quality`/`patient_name_on_doc`) in the same shape; the adapter converts either into `(ClaimSubmission, Dict[str, DocumentClassification])`. `DocumentVerificationAgent` doesn't know or care which one it got — it just checks whether a classification was pre-supplied.

### Decision 18: `ClaimsPipeline.run()` never raises — genuine failures degrade the returned `Claim`
Mirrors the assignment's explicit graceful-failure requirement ("must continue with whatever it has, reflect the degraded state in the output"). Each stage is wrapped individually; a caught exception (AI timeout, parse error) is recorded as `FAILED` in the trace, downstream stages get `SKIPPED`, and `_degrade()` returns a `Claim` with `status=BLOCKED` and an explanatory `user_message` — never an unhandled exception bubbling to FastAPI's generic 500 handler. Verified live against the real (misconfigured) Gemini API — see Known Issues/verification below.

### Decision 19: Per-stage trace design — `COMPLETED` for a correct "blocked" verdict, `FAILED` only for real errors
A document-verification agent that correctly finds a missing document succeeded at its job; that's a `COMPLETED` event with the verdict in `metadata`, never `FAILED`. `FAILED` is reserved for genuine AI/infra problems. Exactly one `PIPELINE`-component event summarises each run: `COMPLETED` (reached the end of Phase 2A), `WARNING` (stopped early for an expected business reason), or `FAILED` (stopped because a stage genuinely errored) — three clean, existing-vocabulary outcomes instead of inventing a new trace concept for "blocked." Full rationale in `docs/architecture.md`.

### Decision 20: Default Gemini model is `gemini-flash-latest`, not a pinned dated version
Discovered live once a real API key was added: `gemini-2.5-flash` (the Phase 2A default) returns `404 NOT_FOUND: ... no longer available to new users` for this key/project, even though it's still listed by `models.list()`. Rather than pin to whatever dated version happens to work for one specific key today (which will eventually rot the same way), the default is now `gemini-flash-latest` — an alias Google maintains to always point at their current default flash model. Anyone who needs reproducible behavior across model updates can still pin a specific dated version in their own `.env`.

### Decision 21: `AITraceMetadata` capture required a dedicated `ai_calls` field and an `ai_metadata_fn` hook — it wasn't automatic
Before Decision 21, `AITraceMetadata` existed in the trace schema (Phase 1) but nothing populated it on a successful `DocumentVerificationAgent` classification — the only failure path (an exception) got safe error info, but a success just returned a `DocumentVerificationResult` with no AI-call record attached. Fixed by adding `DocumentVerificationResult.ai_calls: List[AITraceMetadata]` (one entry per real classification call — empty when every document was pre-classified) and a new `ai_metadata_fn` parameter on `ClaimsPipeline._run_stage`, wired only for the `DOCUMENT_VERIFICATION` stage. Verified against a real successful call — see "Real AI Verification" above.

### Decision 22: `DocumentStorage` is an ABC injected into the pipeline, never imported by an agent directly
Mirrors the `AIProvider`/`TraceService` dependency-injection discipline already established (Decisions 1, 10). `DocumentStorage.save()`/`read()`/`delete()` is the entire surface; `LocalFileDocumentStorage` is the only implementation today, but nothing above the ABC knows that. `app/api/v1/claims.py` reads each `UploadFile` once, calls `document_storage.save(...)`, and passes the resulting `storage_reference` into `DocumentInputAdapter.from_uploads()` — `DocumentVerificationAgent` receives a `storage_reference` string and calls `document_storage.read(storage_reference)` when it needs the real bytes for `analyze_document()`, never touching a filesystem path directly. Swapping to S3 later means writing one new class, not touching the pipeline or agents.

### Decision 23: Generated UUID filenames, never the client's original filename, as the storage path
`generate_storage_filename()` produces `{uuid4}.{validated_extension}` — the original filename (`file_name` in the domain model, shown to the user) is stored separately from `storage_reference` (the actual disk path, never exposed over the API). This closes two real risks at once: path traversal (a filename like `../../etc/passwd` never reaches the filesystem layer) and filename collisions (two members uploading a file both named `prescription.jpg` on the same claim). `LocalFileDocumentStorage._resolve()` additionally does a `Path.resolve()` + containment check as defense-in-depth even though the filename is already server-generated.

### Decision 24: Real classification branches on `storage_reference` presence, not a feature flag
`DocumentVerificationAgent._classify_via_ai()` checks `if document.storage_reference:` to decide between `_classify_from_content()` (reads real bytes, calls `AIProvider.analyze_document()`) and `_classify_from_text_only()` (calls `AIProvider.generate_structured()` with just the declared/actual type as a hint). This isn't a config toggle — it's structural: a real upload always has a `storage_reference` (set by `DocumentStorage.save()` in the API layer before the pipeline ever runs), and an evaluation fixture never does (fixtures skip storage entirely, per Decision 17's "one boundary" design). The agent doesn't need to know which one it's looking at; the data shape tells it.

### Decision 25: `DocumentExtractionAgent` has no fixture/text-only path — extraction is always real AI (Phase 2B)
Unlike `DocumentVerificationAgent` (which falls back to a text-only classification for fixtures with no ground truth — Decision 17/24), extraction has no meaningful "fixture" equivalent: `test_cases.json`'s `content` blocks for TC004+ are themselves the *expected extraction output*, not something a fixture path should short-circuit around. A document with no `storage_reference` cannot be extracted at all and becomes a `DocumentExtractionFailure`, not a silent skip. This keeps "was this ever run through the real Gemini model" an honest yes/no — never a code path that could quietly fabricate extraction results for automated tests. Automated tests mock at the `AIProvider` boundary instead (same pattern every other agent's tests already use).

### Decision 26: Six typed Pydantic schemas, not one generic `ExtractedDocumentData`
Phase 0 already defined a generic `ExtractedDocumentData` model (`app/domain/models.py`) as a placeholder. Phase 2B deliberately does **not** extend it — a hospital bill's meaningful fields (line items, subtotal, discount) share almost nothing with a lab report's (test name, result, reference range), so a single generic schema would either be a mostly-empty superset or a lossy lowest-common-denominator. `app/domain/extraction.py` defines six document-type-specific schemas instead, matching `sample_documents_guide.md`'s own per-type field lists, unified only by a shared `ExtractionBase` (confidence/warnings/evidence) and a `Field(discriminator="document_type")` union. `ExtractedDocumentData` is left untouched — a future cleanup could remove it, but that's out of scope here (removing working code nobody asked to remove isn't part of this phase).

### Decision 27: AI-facing extraction schemas use string-typed amounts, not JSON numbers
Every monetary field in `app/ai/prompts/*_extraction.py`'s schemas is `{"type": "string"}` (e.g. `"1500.00"`), not `{"type": "number"}`. Two reasons: (1) it keeps the "empty means not visible" sentinel (`""`) consistent with every other field, avoiding a mixed nullable-number-or-sentinel schema Gemini's subset doesn't reliably support (Decision 8); (2) `app/domain/extraction.py`'s `_to_decimal` validator parses the string directly into `Decimal`, so a JSON float never exists at any point in the pipeline — "never use `float` for money" (invariant #3) is enforced by construction, not by convention.

### Decision 28: Extraction failure isolation is per-document, not per-claim
`DocumentExtractionAgent.run()` wraps each document's extraction in its own `try/except Exception` and never re-raises — one document's AI timeout/rate-limit/malformed response becomes a `DocumentExtractionFailure` and the loop continues. This is a deliberate one-level-down mirror of `ClaimsPipeline.run()`'s own claim-level never-raise guarantee (Decision 18): a claims system that can't tolerate one bad document out of several is not production-quality for an insurer processing thousands of real, messy uploads. Verified live, not just in tests — see "Real AI Verification (Phase 2B)" below, where a genuine Gemini rate-limit error occurred organically mid-testing and was handled exactly as designed.

### Decision 29: Extraction persists as hybrid (typed JSON envelope + denormalised queryable columns), not a second table
`ClaimDocumentORM` already has one row per document (Phase 2A) — a dedicated `extractions` table would just be a 1:1 join for no benefit. Instead: the full typed `DocumentExtractionResult` envelope is stored as JSON on `ClaimDocumentORM.extraction_json` (the source of truth, rehydrated via the discriminated union), while `diagnosis`/`treatment`/`document_date`/`doctor_name`/`total_amount` are denormalised onto real columns at save time for future SQL queryability. Claim-level rollup (failures/skipped/ai_calls/confidence) lives on `ClaimORM.extraction_summary_json`, matching the existing `*_result_json` columns' pattern. Full detail in `docs/component-contracts.md`.

### Decision 30: `ClaimsPipeline`'s extraction agent is optional (`Optional[..] = None`), defaulting to skip
Adding a required 4th constructor argument to `ClaimsPipeline` would have broken every existing caller — `app/evaluation/runner.py` and every pre-2B pipeline test. Making `document_extraction_agent` optional (`None` → `DOCUMENT_EXTRACTION` recorded `SKIPPED`, not attempted) meant zero existing call sites needed to change, and the evaluation runner (which has no real document bytes for `test_cases.json` fixtures) never has to construct an agent it could never legitimately use anyway (see Decision 25).

### Decision 31: Member identity flows through `ValidationResult`, not a second `PolicyRepository` lookup (Phase 2A identity-fix)
When propagating the resolved `Member` from `ClaimValidationAgent` to `CrossDocumentValidationAgent`, two options existed: (a) have `ClaimsPipeline` call `PolicyRepository.get_member()` a second time right before Stage 3, or (b) have `ClaimValidationAgent` — which already looked the member up to check existence — carry that same object forward. Chose (b): added `member: Optional[Member]` to `ValidationResult` and `claim.member = validation_result.member` in the pipeline. This avoids a redundant lookup, uses `Claim.member` (a field that existed since Phase 0 but was always `None` — see the identity-fix section above), and keeps "who resolves member identity" a single responsibility instead of splitting it across two call sites that could drift out of sync.

---

## Files/Directories Created

```
multi_agent_claims_pipeline/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── deps.py
│   │   │   └── v1/
│   │   │       ├── __init__.py
│   │   │       ├── health.py
│   │   │       ├── traces.py
│   │   │       ├── claims.py
│   │   │       ├── schemas.py
│   │   │       └── router.py
│   │   ├── agents/
│   │   │   ├── __init__.py
│   │   │   ├── base_agent.py
│   │   │   ├── claim_validation_agent.py
│   │   │   ├── document_verification_agent.py
│   │   │   ├── cross_document_validation_agent.py
│   │   │   └── document_extraction_agent.py   ← Phase 2B
│   │   ├── ai/
│   │   │   ├── __init__.py
│   │   │   ├── providers/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── base.py
│   │   │   │   ├── anthropic_provider.py  ← ONLY file with Anthropic SDK import
│   │   │   │   ├── gemini_provider.py     ← ONLY file with google-genai SDK import
│   │   │   │   └── factory.py
│   │   │   ├── schemas/
│   │   │   │   ├── __init__.py
│   │   │   │   └── ai_schemas.py
│   │   │   └── prompts/
│   │   │       ├── __init__.py
│   │   │       ├── document_verification.py
│   │   │       ├── extraction_common.py             ← Phase 2B: shared rules/schema helpers
│   │   │       ├── prescription_extraction.py        ← Phase 2B
│   │   │       ├── hospital_bill_extraction.py        ← Phase 2B
│   │   │       ├── lab_report_extraction.py           ← Phase 2B
│   │   │       ├── pharmacy_bill_extraction.py        ← Phase 2B
│   │   │       ├── dental_extraction.py               ← Phase 2B
│   │   │       └── discharge_summary_extraction.py    ← Phase 2B
│   │   ├── config/
│   │   │   ├── __init__.py
│   │   │   ├── settings.py
│   │   │   └── paths.py
│   │   ├── domain/
│   │   │   ├── __init__.py
│   │   │   ├── models.py
│   │   │   ├── errors.py
│   │   │   ├── trace.py
│   │   │   ├── verification.py
│   │   │   └── extraction.py          ← Phase 2B: 6 extraction schemas + envelope
│   │   ├── evaluation/
│   │   │   ├── __init__.py
│   │   │   └── runner.py
│   │   ├── pipeline/
│   │   │   ├── __init__.py
│   │   │   └── pipeline.py
│   │   ├── policy/
│   │   │   ├── __init__.py
│   │   │   ├── policy_engine.py       ← still a stub (coverage decisions, Phase 3)
│   │   │   └── policy_repository.py   ← ✅ implemented (Phase 2A)
│   │   ├── repositories/
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── database.py
│   │   │   ├── trace_models.py
│   │   │   ├── trace_repository.py
│   │   │   ├── claim_models.py
│   │   │   └── claim_repository.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   └── document_input_adapter.py
│   │   ├── storage/
│   │   │   ├── __init__.py
│   │   │   └── document_storage.py     ← DocumentStorage ABC + LocalFileDocumentStorage
│   │   └── tracing/
│   │       ├── __init__.py
│   │       ├── logging.py
│   │       └── service.py
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py
│   │   ├── unit/
│   │   │   ├── __init__.py
│   │   │   ├── test_config.py
│   │   │   ├── test_domain_models.py
│   │   │   ├── test_ai_provider.py
│   │   │   ├── test_trace_domain.py
│   │   │   ├── test_trace_service.py
│   │   │   ├── test_policy_repository.py
│   │   │   ├── test_claim_validation_agent.py
│   │   │   ├── test_document_verification_agent.py
│   │   │   ├── test_cross_document_validation_agent.py
│   │   │   ├── test_document_input_adapter.py
│   │   │   ├── test_document_storage.py
│   │   │   ├── test_verification_domain.py
│   │   │   ├── test_extraction_domain.py         ← Phase 2B (17 tests)
│   │   │   └── test_document_extraction_agent.py ← Phase 2B (12 tests)
│   │   └── integration/
│   │       ├── __init__.py
│   │       ├── test_health.py
│   │       ├── test_trace_persistence.py
│   │       ├── test_trace_api.py
│   │       ├── test_claims_pipeline.py    ← +TestDocumentExtractionStage (Phase 2B)
│   │       ├── test_claims_api.py         ← rewritten for multipart uploads; +extraction/persistence tests (Phase 2B)
│   │       └── test_eval_tc001_tc003.py
│   ├── pyproject.toml
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── vite-env.d.ts
│   │   ├── types/index.ts
│   │   ├── services/api.ts
│   │   ├── hooks/useHealth.ts
│   │   ├── hooks/useClaimTrace.ts
│   │   ├── components/TraceViewer.tsx
│   │   ├── components/TraceViewer.test.tsx
│   │   ├── pages/Dashboard.tsx
│   │   ├── pages/ClaimSubmission.tsx
│   │   ├── pages/ClaimSubmission.test.tsx
│   │   ├── pages/ClaimDetail.tsx
│   │   └── pages/ClaimDetail.test.tsx
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── tsconfig.node.json
├── scripts/
│   └── run_eval.py
├── docs/
│   ├── architecture.md
│   ├── component-contracts.md
│   ├── AI_HANDOFF.md         ← this file
│   ├── tradeoffs.md
│   └── eval-report.md
├── .env.example
├── .gitignore
├── README.md
├── Dockerfile
└── docker-compose.yml
```

---

## Configuration

Required environment variables (place `.env` at the **project root**,
`multi_agent_claims_pipeline/.env` — see Decision 9 above for why):

```bash
AI_PROVIDER=gemini
AI_MODEL=gemini-flash-latest         # see Decision 20 for why not a pinned dated version
GEMINI_API_KEY=...                  # SECRET — never commit
AI_TIMEOUT_SECONDS=60
DATABASE_URL=sqlite+aiosqlite:///./data/claims.db
LOG_LEVEL=INFO
```

To switch back to Anthropic, set `AI_PROVIDER=anthropic`, `AI_MODEL=claude-sonnet-4-5`,
and `ANTHROPIC_API_KEY=sk-ant-...` — no code changes required, this is exactly
what the provider abstraction exists to guarantee.

---

## Tests

```bash
cd multi_agent_claims_pipeline/backend
python -m pytest                    # all tests
python -m pytest tests/unit/ -v     # unit tests only
python -m pytest tests/integration/ -v  # integration tests
```

Frontend tests:
```bash
cd multi_agent_claims_pipeline/frontend
npm run test                        # vitest run
```

Evaluation runner (TC001-TC003 through the real pipeline):
```bash
cd multi_agent_claims_pipeline/backend
python ../scripts/run_eval.py            # all three
python ../scripts/run_eval.py TC001      # a single case
```

**All tests pass** (298 backend: 250 unit + 48 integration; 35 frontend component tests) — up from Phase 2A's 253 backend / 32 frontend; the 286→298 backend increase is the Phase 2A identity-validation gap fix's 12 new regression tests (see "Phase 2A identity-validation gap fixed" above), not new Phase 2B work.

Verified end-to-end on 2026-08-09 (Phase 2A + Real Document Upload correction):
`pytest` (253/253 passing), `vitest run` (30/30 passing), `npm run build`
(TypeScript compiles clean), `scripts/run_eval.py` (TC001/TC002/TC003 all PASS
through the real `ClaimsPipeline` using fixtures), live `uvicorn` +
`POST /api/v1/claims` (multipart) + `GET /api/v1/claims/{id}` +
`GET /api/v1/claims/{id}/trace` all exercised over real HTTP for TC001/TC002/TC003
using **actual uploaded synthetic JPG files** (not fixtures) processed by the real
Gemini API, and the full submission → detail-page → live-trace flow driven
end-to-end in a real browser for TC003 (real file selection via the file input,
real multipart submit, real classification and cross-document-mismatch result
rendered) — see "Real Document Upload Verification" below.

Also verified: a direct `sqlite3` query against `data/claims.db` confirming
`claims` and `claim_documents` are populated correctly after a live submission.

Verified end-to-end again on 2026-08-09/10 (Phase 2B): `pytest` (286/286
passing), `vitest run` (35/35 passing), `npm run build` (clean),
`scripts/run_eval.py` (TC001/TC002/TC003 still 3/3 PASS — extraction added
zero regressions), live `uvicorn` + real Gemini extraction on TC004's real
prescription/hospital-bill PDFs, a genuine unrelated document, a genuinely
messy handwritten-style prescription, an organic real Gemini rate-limit
failure handled gracefully, and a full backend restart with data verified
intact — see "Real AI Verification (Phase 2B)" below for the complete
account.

---

## Real AI Verification (Phase 2A, section 45) — ✅ FULLY VERIFIED 2026-08-09

A real `GEMINI_API_KEY` was added to `.env` partway through this phase.
Two rounds of live verification followed.

### Round 1 — invalid key (before the real key was added)
Submitted a claim over real HTTP with a document that had no pre-supplied
classification (`declared_type` only, no `actual_type`), forcing
`DocumentVerificationAgent` down its real-AI branch, using the
`.env.example` placeholder key:
1. **The call genuinely reached Google's Gemini API** — took ~7.4s (a real
   network round-trip, not an instant mock failure) and Google's own
   server responded `400 INVALID_ARGUMENT: API key not valid.` — confirming
   the request was well-formed enough for Google to process and reject
   specifically on auth, not malformed structure.
2. **No API key appeared in the trace, logs, or HTTP response.**
3. **The failure degraded gracefully** — `FAILED` in the trace with
   structured `AIProviderError` info, `DOCUMENT_VERIFICATION`'s result
   stayed `None` (never fabricated as a pass), claim came back as a normal
   `201` with `status=BLOCKED` — not a 500.

### Round 2 — real key, full successful round-trip
With the real key in place, the configured default model
(`gemini-2.5-flash`) turned out to be **rejected with `404 NOT_FOUND: This
model ... is no longer available to new users`** — a real, useful finding:
the key authenticated fine (proving it's genuinely valid), but that
specific dated model version isn't available for this key/project.
Queried `client.aio.models.list()` live and switched the default to
**`gemini-flash-latest`** (an alias that always resolves to Google's
current default flash model — see Decision 20) in `.env`, `.env.example`,
and `Settings.ai_model`'s default.

With that model, submitted a claim (`CLM-4B588B3B`) with two documents and
no pre-supplied classification. Confirmed via the live `GET
/api/v1/claims/{id}` and `GET /api/v1/claims/{id}/trace` responses:
1. **Both documents genuinely classified by Gemini** — `source: "ai"` on
   both, `PRESCRIPTION`/`HOSPITAL_BILL` correctly identified from
   filename + declared type alone; `quality: "UNKNOWN"` and `patient_name:
   null` on both (the model correctly declined to guess what it couldn't
   see from text alone — exactly what the prompt's "be conservative, never
   invent" instructions asked for).
2. **Structured output parsed correctly** into `DocumentClassification`
   with no `ExtractionError`.
3. **Provider/model information captured appropriately** — the
   `DOCUMENT_VERIFICATION` `COMPLETED` trace event's `ai_metadata` field
   now shows `{"provider": "gemini", "model": "gemini-flash-latest",
   "latency_ms": 5765.0, "input_tokens": 161, "output_tokens": 48}` — real
   values, not fabricated. (This required adding an `ai_calls: List[AITrace
   Metadata]` field to `DocumentVerificationResult` and passing it through
   `_run_stage`'s new `ai_metadata_fn` parameter — see Decision 21; before
   this, `AITraceMetadata` existed in the trace schema but nothing
   populated it on a successful classification.)
4. **No API key appeared anywhere** — grepped the full trace response for
   the literal key value: zero matches.
5. **The pipeline reached the end of Phase 2A cleanly** —
   `status=PROCESSING`, `document_verification_result.status=PASS`,
   `cross_document_validation_result.status=PASS` (correctly `PASS` with
   an explanatory message, not a mismatch — neither document had an
   extractable patient name to compare).

**Nothing outstanding** — all five of section 45's checklist items are now
verified against a real, successful call.

---

## Real Document Upload Verification (correction, 2026-08-09)

The "Real AI Verification" round above (Phase 2A's first pass) exercised the
AI-classification *code path* but with `declared_type`/no real bytes — it
proved the AI provider integration worked, not that a real uploaded file
flowed through it. After the correction, TC001/TC002/TC003 were re-run
against the live server using **actual synthetic JPG images** (generated
with Pillow — real prescription/bill-style text rendered onto real JPEG
files, including a genuinely blurred one for TC002), submitted as real
`multipart/form-data` uploads:

1. **TC001 (wrong document type)** — two real prescription-image JPGs
   uploaded. Gemini correctly classified both as `PRESCRIPTION` from actual
   image content (`confidence: 0.98` each), pipeline stopped at
   `DOCUMENT_VERIFICATION` with `status=BLOCKED` and a message naming both
   the uploaded type and the missing required type.
2. **TC002 (unreadable document)** — one clear prescription JPG + one
   deliberately blurred bill JPG (downscaled 20x then upscaled, genuinely
   unreadable, not a flag). Gemini classified the good one as `PRESCRIPTION`
   / `GOOD` and the blurry one as `UNKNOWN` / `UNREADABLE`
   (`confidence: 0.1`); claim came back `status=DOCUMENTS_PENDING` (not
   rejected), asking for re-upload.
3. **TC003 (different patients)** — a prescription JPG rendered with
   "Patient: Rajesh Kumar" and a bill JPG rendered with "Patient: Arjun
   Mehta". Gemini extracted both names correctly from the image content
   (`confidence: 0.98` each); `CrossDocumentValidationAgent` correctly
   flagged the mismatch, citing both actual extracted names in the message.
   Reproduced twice: once via direct HTTP multipart, once by driving the
   **actual React UI in a browser** — selected both real files through the
   file input, clicked Submit, and watched the real classification (Type /
   Quality / Patient / Confidence per document) and the cross-document
   mismatch render on the claim detail page, with the full trace timeline
   showing real `ai_calls_made`/`latency_ms` values and no raw file bytes,
   base64 data, or the API key anywhere in the trace or logs.

Also confirmed directly: the uploaded files are persisted to
`data/uploads/{claim_id}/{uuid}.{ext}` under generated UUID filenames (never
the client's original filename), and `storage_reference` never appears in
any API response (checked programmatically against all three responses).

The stale pre-correction `data/claims.db` (missing the new document
columns) caused one `sqlite3.OperationalError` during this verification —
expected, since Phase 2A's SQLite setup has no migration story yet
(`Base.metadata.create_all` only creates missing tables, it doesn't alter
existing ones). Fixed by deleting the gitignored dev database so it
regenerated with the current schema; not a code bug, and noted here in case
a future agent hits the same thing after a domain-model column change.

---

## Real AI Verification (Phase 2B) — ✅ FULLY VERIFIED 2026-08-09/10

Manual testing used the real deployed `uvicorn` server, a real `GEMINI_API_KEY`,
and (per the assignment materials) the actual `sample_documents/` fixture
PDFs/images — never a fixture path through the extraction agent itself
(Decision 25 — extraction has no fixture path to bypass with).

### 1. TC004's real prescription + hospital bill (member EMP001, CONSULTATION, ₹1500, 2024-11-01)
Submitted `F007_prescription_rajesh.pdf` + `F008_hospital_bill_rajesh.pdf`
(the real assignment fixtures, not synthetic) exactly as specified.
Result: `status=PROCESSING`, both documents extracted with `source: "ai"`:
- **Prescription** — patient "Rajesh Kumar" (age 39, gender Male), doctor
  "Dr. Arun Sharma" (reg. `KA/45678/2015`, "MBBS, MD (Internal Medicine)"),
  diagnosis "Viral Fever", both medications (Paracetamol 650mg 1-1-1 ×5
  days, Vitamin C 500mg 0-0-1 ×7 days) and both investigations (CBC, Dengue
  NS1) extracted correctly, `confidence: 1.0`. One honest warning: "Signature
  and stamp are indicated as typed placeholder text '[Signature & Stamp]'
  rather than actual physical ink signatures/stamps" — the model correctly
  distinguished a text placeholder from a real signature rather than just
  reporting `signature_present: true`.
- **Hospital bill** — hospital "City Clinic, Bengaluru", bill number
  `CMC/2024/08322`, all three line items (Consultation Fee ₹1000, CBC
  ₹300, Dengue NS1 ₹200) and the total ₹1500.00 extracted exactly matching
  the source PDF, `confidence: 1.0`.
- `evidence` on both documents contained genuine verbatim quotes from the
  documents (e.g. `"Total Amount: Rs. 1,500.00"`), not paraphrases.
- No API key anywhere in the response (grepped programmatically).

### 2. Unrelated (non-medical) document — filename-trust and hallucination test
Generated a synthetic, genuinely non-medical PDF (an electronics-store
invoice: "ABC Electronics Store", USB cable + wireless mouse line items,
₹1497 total) and uploaded it **named `hospital_bill.pdf`** alongside a
real prescription, for a CONSULTATION claim. Result: Gemini correctly
classified the actual content as `UNKNOWN` (confidence 0.95) despite the
deceptive filename — proving filename is never trusted as evidence, per
the extraction prompts' explicit rule #11. Because `HOSPITAL_BILL` was
still genuinely missing, the pipeline stopped at `DOCUMENT_VERIFICATION`
(`BLOCKED`) *before* extraction ever ran — extraction never attempted to
invent electronics-store data as if it were medical, and never got the
chance to, since Phase 2A's early-stop logic caught it first (exactly the
intended layering — see docs/architecture.md "Document Extraction").

### 3. Genuinely messy document + an organic real AI failure
Paired `sample_documents/EXTRAS/handwritten_style_prescription.jpg` (a
deliberately messy/handwritten-style test fixture, for patient "Ramesh
Nair") with a synthetic matching-patient hospital bill, for a CONSULTATION
claim. Result: `status=PROCESSING`, but with a **real, organic** failure —
not simulated: `AI provider 'gemini' rate limit exceeded` on the second
document's extraction call (a side-effect of the rapid-fire real API calls
made during this verification round). This turned out to be an excellent,
unplanned test of Decision 28:
- The claim did **not** crash and did **not** get blocked — `status`
  stayed `PROCESSING`.
- The prescription (the document that succeeded) extracted correctly
  despite being genuinely messy: diagnosis "HTN — Hypertension, Grade 1",
  both medications (Amlodipine 5mg, Telmisartan 40mg) with dosage/duration,
  doctor "Dr. V. Krishnaswamy" with registration number — `confidence: 0.98`.
- `extraction_result.has_failures = true`, `extraction_result.failures =
  [{"file_id": ..., "document_type": "HOSPITAL_BILL", "reason": "AI
  provider 'gemini' rate limit exceeded."}]`.
- `extraction_result.confidence = 0.98` — the minimum across *successful*
  extractions only, not fabricated for the failed one.
- The trace's `DOCUMENT_EXTRACTION` event was `COMPLETED` (not `FAILED`) —
  correct per Decision 19/28: the agent did its job, it just couldn't
  finish every document.
- `claim.user_message`: "...we extracted the information we could from
  your documents, but 1 document(s) could not be fully processed — a team
  member may need to review this claim manually..." — generated from the
  structured result, not hardcoded.

### 4. Restart persistence (assignment section, "Restart Persistence")
Stopped the live `uvicorn` process (`Stop-Process`) and started a fresh
one against the same (non-deleted) `data/claims.db`. Re-fetched both
claims above via `GET /api/v1/claims/{id}` and `GET /api/v1/claims/{id}/trace`:
- The TC004 claim: both documents' full extraction (diagnosis, doctor,
  medications, line items, total) round-tripped exactly, confirming
  `DocumentExtractionResult.model_validate(row.extraction_json)`'s
  discriminated-union rehydration works correctly from real persisted data,
  not just in unit tests with hand-built fixtures.
- The degraded claim: `has_failures`, the specific failure reason, and the
  one successful extraction's full content all survived the restart intact.
- The trace (10 events) survived intact and in the same order.

**Nothing outstanding** — every claim made about extraction (real AI,
structured validation, provenance capture, graceful per-document failure,
persistence, restart survival) is backed by a real, live verification run
above, not just automated tests with fakes.

---

## Known Issues

1. **Gemini `response_schema` is not full JSON Schema** — see Decision 8. Extraction
   schemas designed in Phase 1 must stick to the OpenAPI-3.0 subset Gemini supports
   (no `$ref`, `oneOf`, `allOf`). Revisit if this becomes limiting.

2. **Integration tests start a real FastAPI app** — The health endpoint tests use
   `ASGITransport` which triggers the full lifespan (DB init, AI provider init).
   With a fake API key, the AI provider will fail to authenticate but the app still
   starts. This is correct behavior for Phase 0 (health endpoint doesn't make live
   AI calls).

3. **SQLite concurrent writes** — SQLite with aiosqlite has WAL mode limitations
   under high concurrency. For Phase 2+ evaluation with many concurrent test cases,
   consider PostgreSQL.

4. **`AsyncClient` + `ASGITransport` never triggers FastAPI's lifespan** — discovered
   while writing `test_trace_api.py`. `init_database()` only runs under a real ASGI
   server (uvicorn) or an explicit lifespan manager; the health-check tests never
   surfaced this because `/health` doesn't touch the database. Any new integration
   test that touches the database must call `init_database()`/`close_database()`
   explicitly in its fixture (see `test_trace_api.py`'s `client` fixture for the
   pattern) rather than relying on the app's lifespan to have run. Production is
   unaffected — verified live under real `uvicorn`.

5. **`TraceService` has no dedicated error type for persistence failures** — if the
   `sink` (`TraceRepository`) raises (e.g. DB connection lost), that exception
   propagates as whatever SQLAlchemy/aiosqlite raised, not a `ClaimsSystemError`.
   Acceptable for Phase 1 (no pipeline exists yet to need graceful degradation from
   a tracing failure specifically); revisit if Phase 2 wants tracing failures to
   never take down claim processing.

6. **Frontend `npm audit` reports 7 vulnerabilities** (1 critical, 1 high, 5
   moderate) after adding `vitest`/`jsdom`/`@testing-library/*` for the
   `TraceViewer` test. The production-affecting one is `react-router-dom`
   (moderate, open-redirect-class) — pre-existing from Phase 0, fix requires a
   major version bump (v6→v7), not applied here to avoid an unrequested breaking
   change. The critical/high ones are transitive dev-only tooling (vite/esbuild
   dev-server CVEs that only matter if a dev server is exposed publicly). Run
   `npm audit` before a production deploy and decide then.

7. **Document classification is text-only (filename + declared type), not real
   OCR/vision** — no file-upload pipeline exists yet in Phase 2A, so the one real
   AI call `DocumentVerificationAgent` makes has very little to go on. **Fully
   verified working with a real key** (see "Real AI Verification" above) — the
   model correctly self-reports `quality: UNKNOWN` rather than guessing, which is
   the right behavior given how little it has to go on; it's real document
   *understanding* that's still missing, not a broken integration.
   `app/ai/prompts/document_verification.py`'s `hint_text` parameter is the
   designed extension point for OCR text / inline image parts.

8. **`LATE_SUBMISSION` (submission-deadline) validation is not implemented** —
   deliberate scope decision, see Decision 15. `RejectionReason.LATE_SUBMISSION`
   already exists in the domain error vocabulary for whenever this is added.

9. **Cross-document patient-name matching is exact (case/whitespace-insensitive),
   not fuzzy** — "Rajesh Kumar" vs. "Raj Kumar" would not match. Documented
   limitation in `docs/component-contracts.md`, not a bug; revisit if real OCR
   output turns out to need typo tolerance.

10. **`ClaimRepository`'s Decimal columns use `Numeric(12, 2)`** — sufficient for
    the assignment's INR amounts (max seen: ₹50,000 annual OPD limit) but worth
    widening if a future policy uses larger sums insured.

11. **Gemini's real classification latency is ~5s per document** with no
    pre-supplied ground truth (two sequential calls took ~10.6s total for a
    2-document claim in live testing) — `DocumentVerificationAgent` classifies
    documents one at a time, not concurrently. Fine for Phase 2A single-claim
    testing; worth parallelizing (`asyncio.gather`) before real submission volume.

12. **Real multimodal classification is slower still — ~20-40s per document**
    (vs. ~5s for the text-only fallback), since Gemini is now actually reading
    image bytes rather than a filename/declared-type hint. A 2-document TC001
    submission took ~60s end-to-end in live testing. Acceptable for Phase 2A;
    worth surfacing upload progress in the UI (currently just a "Submitting…"
    label) if this becomes a real-usage complaint.

13. **No SQLite migration story** — `Base.metadata.create_all()` (run at
    startup) only creates tables that don't exist yet; it never alters an
    existing table's columns. Any future domain-model column change requires
    manually deleting the gitignored dev `data/claims.db` (or adding real
    migrations, e.g. Alembic) before the new schema takes effect locally. Hit
    and resolved during the Real Document Upload correction — see "Real
    Document Upload Verification" above.

14. **`DocumentStorage` is local-filesystem-only** — `LocalFileDocumentStorage`
    is the only implementation; the `DocumentStorage` ABC is deliberately
    designed to be S3-swappable later (see Decision 22), but no S3 adapter
    exists yet. Fine for local dev/single-instance deployment; required before
    any horizontally-scaled or ephemeral-filesystem deployment (e.g. most PaaS
    containers).

15. **Extraction runs sequentially, one document at a time** (Phase 2B) —
    same limitation Known Issue 11 already noted for classification, now
    also true of `DocumentExtractionAgent.run()`'s per-document loop.
    Extraction is slower per call than classification (more output tokens —
    the TC004 verification saw ~7-8s per document vs. ~3-5s for
    classification), so a claim with several documents pays that cost
    serially. Worth parallelizing (`asyncio.gather`, with care around
    real-world provider rate limits — see issue 16) before real submission
    volume; not done here to keep the failure-isolation logic simple and
    easy to verify for this phase.

16. **No retry/backoff on transient AI failures (timeout, rate limit)** —
    `DocumentExtractionAgent` correctly *isolates* a failure to one document
    (Decision 28, verified live with a real rate-limit error — see "Real AI
    Verification (Phase 2B)"), but it does not retry that document once.
    A production system would likely want one bounded retry with backoff
    before giving up and recording a `DocumentExtractionFailure`,
    especially for rate limits (as opposed to e.g. auth failures, which
    retrying can't fix). Not implemented here — flagged rather than guessed
    at, per the assignment's "if stuck, make an assumption and document it"
    guidance.

17. **`ExtractedDocumentData` (Phase 0 placeholder, `app/domain/models.py`)
    is now dead code** — Phase 2B built six document-type-specific schemas
    in `app/domain/extraction.py` instead (see Decision 26) rather than
    filling in this generic placeholder. It's unused but left in place;
    removing it is a trivial future cleanup, not done here since deleting
    working (if unused) code wasn't asked for and isn't this phase's job.

18. **Line-item/medication/test quantities have no unit validation** — e.g.
    a `LineItem.quantity` of `"abc"` silently becomes `None` via
    `_to_decimal`'s best-effort parsing rather than raising or flagging a
    warning. Acceptable for Phase 2B (extraction, not validation — the AI
    is instructed to only output real digits), but worth a `warnings` entry
    from the parser itself (not just the AI) if malformed AI output turns
    out to be a real occurrence at higher volume.

### Resolved this session
- ~~Node.js not installed~~ — installed; `npm install` and `npm run build` verified working.
- ~~`tsconfig.node.json` missing `composite: true`~~ (and conflicting `noEmit: true`) —
  fixed; this was blocking `npm run build` (`tsc` project-reference errors TS6306/TS6310)
  regardless of Node.js availability.
- ~~`/health` always checked `anthropic_api_key` regardless of configured provider~~ —
  fixed in `app/api/v1/health.py`; it now reads `{provider}_api_key` dynamically.
  This would have silently reported `"unconfigured"` for Gemini (or any non-Anthropic
  provider) forever.
- ~~Root-level `.env` was silently ignored~~ — see Decision 9.
- ~~`opd_categories` (lowercase keys) vs. `document_requirements` (uppercase keys)
  case mismatch in `policy_terms.json`~~ — `PolicyRepository.has_category` matches
  case-insensitively; would otherwise have made every category structurally
  "unsupported."
- ~~Dependents (`DEP001`, etc.) in `policy_terms.json` have no `join_date` of
  their own~~ — `PolicyRepository` infers it from the primary member via a
  two-pass load.
- ~~`policy_terms.json`/`test_cases.json`/`sample_documents_guide.md` are not
  actually inside `multi_agent_claims_pipeline/`~~ — see Decision 14;
  `app/config/paths.py` resolves them from the repo root.
- ~~`TraceViewer`'s `MetadataChips` rendered object-valued metadata as
  `"[object Object]"`~~ — found live in the browser testing TC003 (`patient_names`
  is a dict); fixed to `JSON.stringify`, regression test added.
- ~~`DocList` in `ClaimDetail.tsx` used `key={item}`, breaking on duplicate
  document types~~ — found live in the browser testing TC001 (two PRESCRIPTION
  documents); fixed to `key={`${item}-${idx}`}`.
- ~~`.replace('_', ' ')` in `ClaimSubmission.tsx` only replaced the first
  underscore~~ (`PRE_AUTH_LETTER` displayed as "PRE AUTH_LETTER") — found live in
  the browser; fixed to a global regex everywhere it's used.
- ~~No real `GEMINI_API_KEY` had been exercised~~ — a real key was added to
  `.env`; see "Real AI Verification" above for the full round of live testing
  this triggered.
- ~~Default model `gemini-2.5-flash` returns `404: no longer available to new
  users` for this API key~~ — found live the moment a real key was tested;
  switched default to `gemini-flash-latest`. See Decision 20.
- ~~`AITraceMetadata` was never actually populated on a successful AI
  classification~~ — found while doing the real-key verification (Decision 20
  surfaced it: without a real key, every prior test hit the *failure* path,
  which already attached error info; nothing had exercised the *success* path
  far enough to notice the metadata was always `null`). Fixed — see Decision 21.

---

## Things Future Agents Must NOT Break

1. **NEVER import anthropic/openai/genai SDK outside `app/ai/providers/`** — This is the fundamental isolation rule. `anthropic` may only appear in `anthropic_provider.py`; `google.genai` may only appear in `gemini_provider.py`.
2. **NEVER hardcode policy rules** — All rules come from `policy_terms.json`.
3. **NEVER use `float` for money** — Always use `Decimal`.
4. **NEVER let the LLM be the final authority on financial calculations** — PolicyEngine + FinancialCalculationService handle this deterministically.
5. **NEVER change the `ClaimsSystemError.recoverable` semantics** — The orchestrator depends on this.
6. **NEVER put database logic in domain models** — Domain models are pure Pydantic.
7. **NEVER commit `.env` or API keys** — `.gitignore` covers this but double-check.
8. **NEVER remove the `get_settings.cache_clear()` in test fixtures** — Tests will bleed config across each other.
9. **NEVER invent a free-form trace component/event-type string** — use `TraceComponent`/`TraceEventType` from `app/domain/trace.py`. If a new component is genuinely needed, add it to the enum; don't pass a raw string into `TraceEvent`.
10. **NEVER put raw documents, full LLM prompts, or full LLM responses into `TraceEvent.metadata`** — see `docs/architecture.md` §6 for why. Summarize instead (`{"document_type": ..., "quality": ...}`).
11. **NEVER bypass `redact_metadata` when constructing a `TraceEvent` directly** — always go through `TraceService`'s methods (`started`/`completed`/`failed`/`warning`/`skipped`/`span`), never construct+persist a `TraceEvent` by hand, or secret-redaction is skipped.
12. **NEVER change `TraceRepository` ordering to sort by `timestamp`** — order by the autoincrement `id` (exposed as `TraceEvent.sequence`). Two events can share a timestamp at pipeline speed; `id` is the only unambiguous order.
13. **NEVER hardcode document requirements per category** — always `PolicyRepository.get_document_requirements(category)`, never `if category == "CONSULTATION": required = [...]`.
14. **NEVER put fixture-specific logic (test-case IDs, expected outcomes) inside an agent or `ClaimsPipeline`** — that belongs in `app/evaluation/runner.py`'s checkers. Agents must not know "TC001" exists.
15. **NEVER let `ClaimsPipeline.run()` raise** — a genuine stage failure must be caught and turned into a degraded `Claim`, never propagate to FastAPI's generic 500 handler. See Decision 18.
16. **NEVER treat a document-verification "blocked"/"needs resubmission" verdict as a `FAILED` trace event** — the agent succeeded at its job; only genuine AI/infra errors are `FAILED`. See Decision 19.
17. **NEVER construct a `ValidationIssue`/rejection message as a hardcoded per-test-case string** — always build it from the structured result (missing/wrong/quality_issues, patient names) so the same code works for every category and every case, not just TC001-TC003.
18. **NEVER let one document's extraction failure stop the others, or the claim** — `DocumentExtractionAgent.run()` must keep isolating failures per-document (Decision 28); a single AI timeout/rate-limit/parse error must become a `DocumentExtractionFailure`, never propagate and abort the whole claim.
19. **NEVER add a `null`/nullable-union field to an extraction schema** — Gemini's `response_schema` subset doesn't reliably support it (Decision 8). Use the established sentinel convention instead (`""` strings/dates/amounts, `[]` lists, `"UNCLEAR"` tri-state) and add the corresponding `_empty_to_none`/`_to_decimal`/`_to_tristate_bool` validator in `app/domain/extraction.py`.
20. **NEVER make `document_extraction_agent` a required `ClaimsPipeline` constructor argument** — it must stay `Optional[...] = None` (Decision 30) so the evaluation runner and any test that predates Phase 2B keeps working unmodified.
21. **NEVER let `ClaimValidationAgent` resolve a `Member` and then discard it** — this was the root cause of the Phase 2A identity-validation gap (see that section above). The resolved `Member` must keep flowing through `ValidationResult.member` → `Claim.member` → `CrossDocumentValidationAgent`'s `member` parameter (Decision 31). If a future refactor changes how member resolution works, keep this propagation path intact or re-introduce the exact bug that was just fixed.

---

## Phase 1 Summary (complete)

Built the trace/observability infrastructure every future pipeline
component will use — deliberately *before* any claims logic exists, so
explainability isn't retrofitted later. See "Tracing / Observability
Stack" above for the file list and `docs/architecture.md` §6 /
`docs/component-contracts.md` for the full design.

Explicitly NOT implemented in Phase 1 (as scoped): claim validation
rules, document verification/extraction, OCR, policy evaluation,
financial calculations, fraud analysis, final decisions, the claims
submission endpoint, or the 12 test cases. `TraceViewer`/`useClaimTrace`
exist and are tested but aren't mounted into any page yet.

---

## Phase 2A Summary (complete)

Built the first real claims-processing vertical slice: `ClaimValidationAgent`
→ `DocumentVerificationAgent` → `CrossDocumentValidationAgent`, orchestrated
by `ClaimsPipeline` with early stopping, full Phase-1 trace integration, and
graceful degradation on AI/infra failure. `TC001`/`TC002`/`TC003` all PASS
through the real pipeline (verified via `app/evaluation/runner.py`, both as
an automated test and a standalone script). `POST/GET /api/v1/claims` are
live, persisted, and wired into a real (if unpolished) React submission +
detail UI that mounts the Phase 1 `TraceViewer` against real backend data.

Explicitly NOT implemented in Phase 2A (as scoped): policy/coverage
decisions, waiting periods, exclusions, co-pay, network discounts,
pre-authorization, fraud scoring, financial calculations, or any final
decision (`APPROVED`/`PARTIAL`/`REJECTED`/`MANUAL_REVIEW`). `Claim.decision`
stays `None` through this entire phase, by design.

---

## Phase 2B Summary (complete)

Built the fourth pipeline stage, `DocumentExtractionAgent`, running after
cross-document validation: six document-type-specific Pydantic schemas
(`app/domain/extraction.py`) matching `sample_documents_guide.md`'s field
lists, six modular prompt files sharing a common rules/schema-helper
module (`app/ai/prompts/{prescription,hospital_bill,lab_report,
pharmacy_bill,dental,discharge_summary}_extraction.py` +
`extraction_common.py`), always calling the real `AIProvider.analyze_document()`
(no fixture path — Decision 25), with per-document failure isolation so
one bad document never blocks the claim or the others (Decision 28,
verified live against a real, organic Gemini rate-limit error). Persisted
as a hybrid of a full typed JSON envelope plus five denormalised queryable
columns (Decision 29), surfaced through `ClaimResponse`/`ClaimDocumentSummary`,
and rendered in `ClaimDetail.tsx` as a document-type-specific "Extracted
Information" panel — never raw JSON. `ClaimsPipeline`'s new stage is fully
backward-compatible (`document_extraction_agent: Optional[...] = None`,
Decision 30) — the evaluation runner and every pre-2B pipeline test needed
zero changes. TC001/TC002/TC003 all still pass (`3/3`, unchanged) — none of
them reach extraction, by design, since all three stop earlier in the
pipeline. 33 new automated tests (29 unit + 4 integration/API) plus 3 new
frontend tests, all passing alongside the full pre-existing suite
(286 backend / 35 frontend total). Verified end-to-end with real Gemini
calls on the actual assignment fixture documents, a genuine unrelated
(non-medical, deceptively-named) document, a genuinely messy prescription,
an organic real AI failure handled gracefully, and a full backend restart
with data verified intact — see "Real AI Verification (Phase 2B)" above.

Explicitly NOT implemented in Phase 2B (as scoped, per the Phase 2B
brief's own "Do Not Implement Yet" list): `PolicyEngine`, waiting periods,
exclusions, pre-existing conditions, sub-limits, co-pay, network
discounts, pre-authorization decisions, fraud scoring, financial
calculation, or any final decision
(`APPROVED`/`PARTIAL`/`REJECTED`/`MANUAL_REVIEW`). `Claim.decision` stays
`None` through this entire phase too, by design — `DocumentExtractionAgent`
answers "what does the document say," never "is this covered."

---

## Next Phase — Phase 3

### Goal
Implement policy evaluation and the final decision, using
`ClaimValidationAgent`/`DocumentVerificationAgent`/`CrossDocumentValidationAgent`/
`DocumentExtractionAgent`/`ClaimsPipeline` from Phases 2A/2B as the
foundation everything else builds on top of. The extracted structured data
(diagnosis, line items, amounts, dates, doctor details — now real, typed,
and persisted) is exactly what `PolicyEngine`/`FinancialCalculationService`
need as input; Phase 3 consumes `claim.extraction_result`, it doesn't need
to re-derive anything from raw documents.

### Components to Build
1. `PolicyEngine` (fill in the existing stub in `app/policy/policy_engine.py`, built on top of `PolicyRepository`) — deterministic coverage, sub-limits, co-pay, network discount, waiting periods, exclusions, pre-authorization, all from `policy_terms.json`
2. `FraudAnalysisAgent` — same-day claim patterns, high-value flags, monthly claim limits (thresholds already defined in `policy_terms.json`'s `fraud_thresholds`)
3. `FinancialCalculationService` — copay, network discount, limits (Decimal arithmetic, no LLM); TC010 specifically tests discount-before-copay ordering
4. `DecisionGenerationAgent` — synthesise `APPROVED`/`PARTIAL`/`REJECTED`/`MANUAL_REVIEW` (`ClaimDecision` already exists in `app/domain/models.py` from Phase 0, unused until now)
5. `ExplanationAgent` — member-facing explanation of the decision
6. Extend `ClaimsPipeline` with these stages after document extraction, following the same `_run_stage`/early-stop/graceful-degradation pattern (and the same "optional agent, defaults to skip" backward-compatibility trick from Decision 30, if warranted)
7. Extend `POST /api/v1/claims`'s response with decision fields once they exist
8. Extend `ClaimSubmission`/`ClaimDetail` pages to show the decision, financial breakdown, and explanation

### Also required for Phase 3
- ORM model for `decisions` (or extend `claims` — TBD when the shape is known)
- Run all 12 test cases (not just TC001-TC003) against the real Gemini API using real uploaded documents; write `docs/eval-report.md` — Phase 3 is the first phase that can actually attempt TC004-TC012, since they need a real decision to check against
- Consider Alembic (or similar) migrations before further domain-model column changes — see Known Issue 13 (now also relevant to Phase 2B's own column additions)
- An S3 (or equivalent) `DocumentStorage` implementation before any horizontally-scaled deployment — see Known Issue 14
- Consider parallelizing per-document AI calls (classification and extraction) and/or a bounded retry for transient failures — see Known Issues 15/16
- Update this document

---

## Assignment Source Files

| File | Do Not Modify |
|------|---------------|
| `assignment.md` (repo root, above `multi_agent_claims_pipeline/`) | ✅ Source of truth — never modify |
| `policy_terms.json` | ✅ Source of truth — never modify |
| `test_cases.json` | ✅ Source of truth — never modify |
| `sample_documents_guide.md` | ✅ Reference only — never modify |
