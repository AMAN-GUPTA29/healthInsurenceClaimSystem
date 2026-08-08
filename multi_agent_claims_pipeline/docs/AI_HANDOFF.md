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

**Phase 1 — Observability & Trace Infrastructure** ✅ COMPLETE
(Phase 0 — Foundation & Architecture ✅ COMPLETE, history preserved below)

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

---

## Implemented Components (Phase 0 + Phase 1)

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
| Pipeline placeholder | `app/pipeline/pipeline.py` | ⬜ stub |
| Policy engine placeholder | `app/policy/policy_engine.py` | ⬜ stub |
| Trace domain models | `app/domain/trace.py` | ✅ |
| TraceService | `app/tracing/service.py` | ✅ |
| TraceEventORM | `app/repositories/trace_models.py` | ✅ |
| TraceRepository | `app/repositories/trace_repository.py` | ✅ |
| Trace API endpoint | `app/api/v1/traces.py` | ✅ |
| Trace Repository DI | `app/api/deps.py` (`TraceRepositoryDep`) | ✅ |

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

Note: `TraceViewer`/`useClaimTrace` are built and tested but **not mounted
into any page** — Phase 1 scope is the reusable foundation only; a claim
detail page to host them arrives with the claims UI in Phase 2.

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
| TraceViewer component (all 5 statuses) | `frontend/src/components/TraceViewer.test.tsx` | ✅ (vitest) |

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
│   │   │       └── router.py
│   │   ├── agents/
│   │   │   ├── __init__.py
│   │   │   └── base_agent.py
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
│   │   │       └── __init__.py
│   │   ├── config/
│   │   │   ├── __init__.py
│   │   │   └── settings.py
│   │   ├── domain/
│   │   │   ├── __init__.py
│   │   │   ├── models.py
│   │   │   ├── errors.py
│   │   │   └── trace.py
│   │   ├── pipeline/
│   │   │   ├── __init__.py
│   │   │   └── pipeline.py
│   │   ├── policy/
│   │   │   ├── __init__.py
│   │   │   └── policy_engine.py
│   │   ├── repositories/
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── database.py
│   │   │   ├── trace_models.py
│   │   │   └── trace_repository.py
│   │   ├── services/
│   │   │   └── __init__.py
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
│   │   │   └── test_trace_service.py
│   │   └── integration/
│   │       ├── __init__.py
│   │       ├── test_health.py
│   │       ├── test_trace_persistence.py
│   │       └── test_trace_api.py
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
│   │   └── pages/Dashboard.tsx
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── tsconfig.node.json
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
AI_MODEL=gemini-2.5-flash           # or gemini-2.5-pro, gemini-2.0-flash
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

**All tests pass** (126 backend: 106 unit + 20 integration; 15 frontend component tests).

Verified end-to-end on 2026-08-09 (Phase 1): `pytest` (126/126 passing), `vitest run`
(15/15 passing), `npm run build` (TypeScript compiles clean), `uvicorn` startup with
`GET /api/v1/health` returning `{"provider": "gemini", ...}`, trace events seeded
directly through `TraceService`/`TraceRepository` against the live running server,
`GET /api/v1/claims/{claim_id}/trace` returning them in correct chronological order
with confidence/metadata/error info intact, and a direct `sqlite3` query against
`data/claims.db` confirming the `trace_events` table exists and is populated
(9 rows for the seeded demo claim). React dashboard still renders correctly in a
browser after the frontend changes (regression-checked).

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

4. **No real Gemini API key has been exercised yet** — `.env` currently holds the
   placeholder from `.env.example`. `initialize()` (client construction) succeeds
   with any non-empty string since the SDK doesn't validate the key until the first
   real call; a live call will fail auth until a real `GEMINI_API_KEY` is set.

5. **`AsyncClient` + `ASGITransport` never triggers FastAPI's lifespan** — discovered
   while writing `test_trace_api.py`. `init_database()` only runs under a real ASGI
   server (uvicorn) or an explicit lifespan manager; the health-check tests never
   surfaced this because `/health` doesn't touch the database. Any new integration
   test that touches the database must call `init_database()`/`close_database()`
   explicitly in its fixture (see `test_trace_api.py`'s `client` fixture for the
   pattern) rather than relying on the app's lifespan to have run. Production is
   unaffected — verified live under real `uvicorn`.

6. **`TraceService` has no dedicated error type for persistence failures** — if the
   `sink` (`TraceRepository`) raises (e.g. DB connection lost), that exception
   propagates as whatever SQLAlchemy/aiosqlite raised, not a `ClaimsSystemError`.
   Acceptable for Phase 1 (no pipeline exists yet to need graceful degradation from
   a tracing failure specifically); revisit if Phase 2 wants tracing failures to
   never take down claim processing.

7. **Frontend `npm audit` reports 7 vulnerabilities** (1 critical, 1 high, 5
   moderate) after adding `vitest`/`jsdom`/`@testing-library/*` for the
   `TraceViewer` test. The production-affecting one is `react-router-dom`
   (moderate, open-redirect-class) — pre-existing from Phase 0, fix requires a
   major version bump (v6→v7), not applied here to avoid an unrequested breaking
   change. The critical/high ones are transitive dev-only tooling (vite/esbuild
   dev-server CVEs that only matter if a dev server is exposed publicly). Run
   `npm audit` before a production deploy and decide then.

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

## Next Phase — Phase 2

### Goal
Implement the actual claims processing pipeline with real AI calls, using
the trace infrastructure built in Phase 1 for every step.

### Components to Build
1. `ClaimValidationAgent` — validate submission, member eligibility, submission deadline
2. `DocumentVerificationAgent` — detect document types, check required documents for category
3. `DocumentExtractionAgent` — OCR + structured data extraction from images/PDFs
4. `CrossDocumentValidationAgent` — patient name matching, date consistency
5. `PolicyEngine` — deterministic coverage, limits, waiting periods, exclusions from policy_terms.json
6. `FraudAnalysisAgent` — same-day claim patterns, high-value flags
7. `FinancialCalculationService` — copay, network discount, limits (Decimal arithmetic, no LLM)
8. `DecisionGenerationAgent` — synthesise final decision
9. `ExplanationAgent` — generate member-facing explanation
10. `ClaimsPipeline` — orchestrate all agents; construct one `TraceService` per claim (via `create_trace_service(claim_id)`) and inject it into every agent; handle component failures via `tracer.span(...)` / `tracer.failed(...)`
11. `POST /api/v1/claims` — claims submission endpoint
12. Claims status endpoint
13. Claim submission UI + a claim detail page that mounts `TraceViewer` via `useClaimTrace`

### Also required for Phase 2
- ORM models for claims, documents, decisions (trace_events already exists — Phase 1)
- Database migrations (alembic or manual) if the schema needs to evolve beyond `create_all`
- Update docs with Phase 2 component contracts (agents, pipeline, policy engine)
- Run all 12 test cases against the real Gemini API; write the eval report
- Update this document

---

## Assignment Source Files

| File | Do Not Modify |
|------|---------------|
| `assignment.md` (repo root, above `multi_agent_claims_pipeline/`) | ✅ Source of truth — never modify |
| `policy_terms.json` | ✅ Source of truth — never modify |
| `test_cases.json` | ✅ Source of truth — never modify |
| `sample_documents_guide.md` | ✅ Reference only — never modify |
