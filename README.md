# Health Insurance Claims Processing System

> **Plum AI Engineer Assignment** — Multi-agent AI pipeline for health insurance claim evaluation.

---

## Overview

This system automates end-to-end health insurance OPD claim processing. A member submits a claim (member details, treatment category, claimed amount, one or more document uploads); the system verifies the right documents were provided, extracts structured information from them, evaluates the claim against the member's policy, calculates the payable amount, checks for fraud signals, and produces a final decision (`APPROVED` / `PARTIAL` / `REJECTED` / `MANUAL_REVIEW`) with an approved amount, a reason, a confidence score, and a full explainable trace.

A multi-agent architecture with a hard separation between **AI-assisted stages** (document classification and extraction — real Gemini/Claude calls, structured and validated) and a **deterministic core** (policy evaluation, financial calculation, fraud thresholds, and the final decision itself — plain Python, never an LLM). See `ARCHITECTURE.docx` for the full design rationale, `COMPONENT_CONTRACTS.docx` for every component's precise interface, and `docs/tradeoffs.md` for the judgment calls made along the way.

**Current Status: Phase 3 — Final Audit, Correctness & Submission Readiness ✅ COMPLETE.** All 12 official test cases from `test_cases.json` pass through the real pipeline (`docs/eval-report.md`, `EVAL_REPORT.docx`).

---

## Architecture

```
multi_agent_claims_pipeline/
├── backend/               # FastAPI Python backend
│   ├── app/
│   │   ├── api/           # REST endpoints (FastAPI routers)
│   │   ├── agents/        # Pipeline agents (BaseAgent + implementations)
│   │   ├── ai/
│   │   │   ├── providers/ # AIProvider ABC + vendor adapters (only files that import vendor SDKs)
│   │   │   ├── schemas/   # Vendor-agnostic AI request/response types
│   │   │   └── prompts/   # Prompt templates (classification, extraction, explanation)
│   │   ├── domain/        # Core domain models + error hierarchy
│   │   ├── policy/        # Deterministic policy engine + policy_terms.json repository
│   │   ├── services/      # FinancialCalculationService, DocumentInputAdapter
│   │   ├── pipeline/      # Multi-agent orchestrator (ClaimsPipeline)
│   │   ├── evaluation/    # Evaluation harness — runs test_cases.json through the real pipeline
│   │   ├── repositories/  # Data access layer (async SQLAlchemy)
│   │   ├── storage/       # Document storage abstraction (local disk; S3-ready interface)
│   │   ├── tracing/       # Structured observability (TraceService)
│   │   ├── config/        # Typed settings (Pydantic BaseSettings)
│   │   └── main.py        # FastAPI app factory
│   └── tests/
│       ├── unit/          # Unit tests (one per agent/service/domain area)
│       └── integration/   # Integration tests (full pipeline, API, persistence, official 12-case eval)
├── frontend/              # React + TypeScript + Vite UI
├── docs/                  # Architecture, component contracts, trade-offs, eval report, handoff
├── scripts/
│   └── run_eval.py        # Runs all 12 official test cases through the real pipeline
├── data/                  # SQLite DB + uploaded document storage (gitignored)
├── policy_terms.json      # Source of truth for all policy rules (repo root — never modified)
├── test_cases.json        # 12 official evaluation test cases (repo root — never modified)
└── sample_documents_guide.md
```

---

## Key Design Principles

1. **AI provider abstraction** — Agents depend only on `AIProvider` (ABC). Vendor SDK imports exist ONLY inside their respective adapter: `app/ai/providers/anthropic_provider.py` (default — Claude Sonnet) and `app/ai/providers/gemini_provider.py` (alternate). Switching `AI_PROVIDER=anthropic` → `AI_PROVIDER=gemini` is a configuration change, not a code change.
2. **No hardcoded policy rules** — every rule (limits, waiting periods, exclusions, network hospitals, fraud thresholds) is read from `policy_terms.json` through `PolicyRepository`.
3. **Deterministic financials and decisions** — the LLM never computes an amount, decides coverage, or chooses the final decision. `PolicyEngine`, `FinancialCalculationService`, `FraudAnalysisAgent`, and `DecisionGenerationAgent` make zero AI calls; the LLM (`ExplanationAgent`) only writes up an already-decided outcome in plain language, and cannot change it.
4. **Dependency injection** — agents receive `AIProvider`/`PolicyRepository`/etc. via constructor, never via module globals.
5. **Graceful degradation** — every significant component can fail without crashing the pipeline; a failure is recorded in the trace, confidence degrades accordingly, and the claim still reaches a result (or an honest "needs manual review").
6. **Full observability** — every stage emits structured `TraceEvent`s (`STARTED`/`COMPLETED`/`FAILED`/`SKIPPED`/`WARNING`), persisted and replayable via `GET /api/v1/claims/{claim_id}/trace` — no raw documents, prompts, AI responses, or secrets are ever stored in a trace (automatic redaction, see `app/tracing/service.py::redact_metadata`).

---

## Local Setup

### Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend)
- An Anthropic API key (default provider — Claude Sonnet) — get one at [console.anthropic.com](https://console.anthropic.com/), **or** a Google Gemini API key (see "Switching AI provider" below)

### Backend

```bash
cd multi_agent_claims_pipeline/backend

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Configure environment (.env lives at the project root)
cp ../.env.example ../.env
# Edit ../.env and set ANTHROPIC_API_KEY=your-key-here

# Run the backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Backend available at: http://localhost:8000
API docs: http://localhost:8000/docs
Health check: http://localhost:8000/api/v1/health

### Frontend

```bash
cd multi_agent_claims_pipeline/frontend

# Install dependencies
npm install

# Run dev server
npm run dev
```

Frontend available at: http://localhost:5173 (proxies API calls to the backend at `http://localhost:8000` — see `VITE_API_BASE_URL` if you need to point it elsewhere).

### Switching AI provider (Claude ↔ Gemini)

No code changes are required. In `.env`:

```bash
# Anthropic Claude Sonnet (default — cost-efficient, not Opus)
AI_PROVIDER=anthropic
AI_MODEL=claude-sonnet-4-5
ANTHROPIC_API_KEY=your-anthropic-key

# Google Gemini (alternate)
AI_PROVIDER=gemini
AI_MODEL=gemini-flash-latest
GEMINI_API_KEY=your-gemini-key
```

Restart the backend after changing `.env`. `app/ai/providers/factory.py` selects the concrete provider at startup based on `AI_PROVIDER` alone; no agent, prompt, or pipeline code references either vendor SDK directly. Verified live both ways (real Claude API call, a real end-to-end claim through the complete pipeline, and a config-only factory-selection check confirming both directions work without touching application code).

---

## Environment Variables

See [.env.example](.env.example) for all configuration options.

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `development` | Application environment |
| `DATABASE_URL` | SQLite (`./data/claims.db`) | SQLAlchemy async URL — change to a PostgreSQL URL for production, see `ARCHITECTURE.docx` "15. Limitations & Scaling Notes" |
| `AI_PROVIDER` | `anthropic` | AI provider (`anthropic`, `gemini`) |
| `AI_MODEL` | `claude-sonnet-4-5` | Model identifier for the configured provider |
| `AI_TIMEOUT_SECONDS` | `60` | AI API call timeout |
| `ANTHROPIC_API_KEY` | — | **Required** when `AI_PROVIDER=anthropic` |
| `GEMINI_API_KEY` | — | **Required** when `AI_PROVIDER=gemini` |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `CORS_ORIGINS` | `http://localhost:5173`, `http://localhost:3000` | Allowed frontend origins — set to your deployed frontend's origin in production |

> `.env` is read from the **project root** (`multi_agent_claims_pipeline/.env`), regardless of whether you launch `uvicorn`/`pytest` from `backend/` or the project root. `policy_terms.json`/`test_cases.json`/`sample_documents_guide.md` are resolved the same way, one level further up at the **repository root** — see `app/config/paths.py`.

---

## Running Tests

```bash
cd multi_agent_claims_pipeline/backend

# All tests
python -m pytest

# Unit tests only
python -m pytest tests/unit/ -v

# Integration tests only (includes the full 12-case official evaluation)
python -m pytest tests/integration/ -v

# With coverage
python -m pytest --cov=app --cov-report=html
```

```bash
cd multi_agent_claims_pipeline/frontend

npm run test          # component tests (vitest)
npx tsc --noEmit      # type check
npm run build         # production build
```

---

## Running the Official Evaluation

All 12 cases from `test_cases.json`, run through the real, complete pipeline (never modifies `test_cases.json`, never branches on a case ID):

```bash
cd multi_agent_claims_pipeline/backend

python ../scripts/run_eval.py            # all 12 cases, full trace printed for each
python ../scripts/run_eval.py TC008      # a single case

# Or as a committed, CI-enforced regression test (same code path):
python -m pytest tests/integration/test_eval_all_cases.py -v
```

See `docs/eval-report.md` for the current results table (12/12) and methodology.

---

## API Overview

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/v1/health` | Liveness/readiness check, including configured AI provider status |
| `POST` | `/api/v1/claims` | Submit a claim — member details + one or more document files (multipart upload). Returns the full claim record, including decision (if reached) and trace summary. |
| `GET` | `/api/v1/claims/{claim_id}` | Retrieve a previously submitted claim in full — documents, extraction, policy/financial/fraud results, decision, explanation. |
| `GET` | `/api/v1/claims/{claim_id}/trace` | The complete structured trace for a claim — every stage's `STARTED`/`COMPLETED`/`FAILED`/`SKIPPED` event, in order. |

Full interactive schema at `/docs` (Swagger UI) once the backend is running. See `COMPONENT_CONTRACTS.docx` "16. API Contracts" for the precise shape of every response field.

---

## Deployment

No containerized deployment is provided in this repository (Docker was removed as unnecessary for this assignment's scope). To deploy:

- **Backend**: any host that can run `uvicorn app.main:app` behind a process manager (systemd, supervisor, or a PaaS like Render/Railway/Fly.io) — set the environment variables above, point `DATABASE_URL` at a real PostgreSQL instance for anything beyond single-process local use (see `ARCHITECTURE.docx` "15. Limitations & Scaling Notes"), and mount/persist `data/` for document storage.
- **Frontend**: `npm run build` produces a static `dist/` bundle (any static host — Vercel/Netlify/S+CloudFront/nginx); set `VITE_API_BASE_URL` to the deployed backend's URL at build time.
- **Source-of-truth files**: `policy_terms.json`/`test_cases.json`/`sample_documents_guide.md` must be present at the repository root relative to `multi_agent_claims_pipeline/` (see `app/config/paths.py`) — copy them alongside the deployed backend code.

---

## Known Limitations

The most significant:

- **Live Gemini verification is blocked in this development environment** — a corporate SSL-inspection proxy blocks outbound HTTPS to Google's API (`CERTIFICATE_VERIFY_FAILED`; occasionally succeeds, so likely intermittent rather than a hard block). **Live Anthropic/Claude calls are not blocked** — the current default provider (Claude Sonnet) has been verified with real API calls (structured generation, document classification, document extraction, and explanation generation) through the actual running application, not just mocks. TLS verification has never been disabled to work around either provider's connectivity. Every AI-calling component has also been verified to fail gracefully (structured error, reduced confidence, deterministic fallback where applicable — never a crash) in unit tests with fake providers.
- **No database migrations** — schema changes require recreating the SQLite dev database; PostgreSQL + Alembic is the natural next step before further schema evolution.
- **Sequential per-document AI calls** — classification/extraction calls are awaited one at a time per claim, not batched or parallelized. See `ARCHITECTURE.docx` "15. Limitations & Scaling Notes".
- **Session-count and pre-existing-condition tracking** — a small number of policy checks (`max_sessions_per_year`, pre-existing-condition waiting periods) are reported as `WARNING` (honestly "cannot verify"), not computed, since the current data model has no field for the history they'd need.

---

## Assignment Files

| File | Purpose |
|------|---------|
| `policy_terms.json` | Policy rules, limits, exclusions, members. **Source of truth — never modified.** |
| `test_cases.json` | 12 official evaluation test cases with expected decisions. **Source of truth — never modified.** |
| `sample_documents_guide.md` | Document types, formats, extraction requirements. **Reference — never modified.** |
