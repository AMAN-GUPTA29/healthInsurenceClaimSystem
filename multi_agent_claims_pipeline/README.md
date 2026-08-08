# Health Insurance Claims Processing System

> **Plum AI Engineer Assignment** — Production-quality multi-agent AI pipeline for health insurance claim evaluation.

---

## Overview

This system processes health insurance OPD claims through a multi-agent AI pipeline. Each claim passes through document verification, extraction, policy evaluation, fraud analysis, financial calculation, and decision generation.

**Current Status: Phase 2A — Claim Foundation & Early Document Verification**

---

## Architecture

```
multi_agent_claims_pipeline/
├── backend/               # FastAPI Python backend
│   ├── app/
│   │   ├── api/           # REST endpoints (FastAPI routers)
│   │   ├── agents/        # Pipeline agents (BaseAgent + implementations)
│   │   ├── ai/
│   │   │   ├── providers/ # AIProvider ABC + vendor adapters
│   │   │   ├── schemas/   # Vendor-agnostic AI request/response types
│   │   │   └── prompts/   # Prompt templates
│   │   ├── domain/        # Core domain models + error hierarchy
│   │   ├── policy/        # Policy engine (deterministic rule evaluation)
│   │   ├── pipeline/      # Multi-agent orchestrator
│   │   ├── services/      # Application services
│   │   ├── repositories/  # Data access layer
│   │   ├── tracing/       # Structured logging + observability
│   │   ├── config/        # Typed settings (Pydantic BaseSettings)
│   │   └── main.py        # FastAPI app factory
│   └── tests/
│       ├── unit/          # Unit tests (config, domain, AI provider)
│       └── integration/   # Integration tests (health endpoint)
├── frontend/              # React + TypeScript + Vite UI
├── docs/                  # Architecture, contracts, handoff
├── scripts/               # Utility scripts
├── data/                  # Database files (gitignored)
├── policy_terms.json      # Source of truth for all policy rules
├── test_cases.json        # 12 evaluation test cases
├── sample_documents_guide.md
└── docker-compose.yml
```

---

## Key Design Principles

1. **AI provider abstraction** — Agents depend only on `AIProvider` (ABC). Vendor SDK imports exist ONLY inside their respective adapter: `app/ai/providers/gemini_provider.py` (default) and `app/ai/providers/anthropic_provider.py` (alternate).
2. **No hardcoded policy rules** — All rules are loaded from `policy_terms.json`.
3. **Deterministic financials** — The LLM never computes financial calculations.
4. **Dependency injection** — Agents receive `AIProvider` via constructor, never via module globals.
5. **Graceful degradation** — Errors carry `recoverable` flag; pipeline continues with lower confidence.
6. **Structured errors** — Rich error hierarchy for orchestration decisions.

---

## Local Setup

### Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend)
- A Google Gemini API key (default provider) — get one at [Google AI Studio](https://aistudio.google.com/apikey)

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
# Edit ../.env and set GEMINI_API_KEY=your-key-here

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

Frontend available at: http://localhost:5173

### Docker

```bash
# Build and run the full stack
docker compose up --build

# Backend only
docker compose up backend
```

---

## Environment Variables

See [.env.example](.env.example) for all configuration options.

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `development` | Application environment |
| `DATABASE_URL` | SQLite | SQLAlchemy async URL |
| `AI_PROVIDER` | `gemini` | AI provider (`gemini`, `anthropic`, `openai`) |
| `AI_MODEL` | `gemini-2.5-flash` | Model identifier |
| `AI_TIMEOUT_SECONDS` | `60` | AI API call timeout |
| `GEMINI_API_KEY` | — | **Required** for Gemini provider (default) |
| `ANTHROPIC_API_KEY` | — | Required only if `AI_PROVIDER=anthropic` |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `CORS_ORIGINS` | localhost:5173 | Allowed frontend origins |

> `.env` is read from the **project root** (`multi_agent_claims_pipeline/.env`)
> regardless of whether you launch `uvicorn` from `backend/` or the project root.

---

## Running Tests

```bash
cd multi_agent_claims_pipeline/backend

# All tests
python -m pytest

# Unit tests only
python -m pytest tests/unit/ -v

# Integration tests only
python -m pytest tests/integration/ -v

# With coverage
python -m pytest --cov=app --cov-report=html
```

---

## Implementation Status

### ✅ Phase 0 — Complete
- [x] Project structure
- [x] FastAPI application factory
- [x] `GET /api/v1/health` endpoint
- [x] Typed configuration (Pydantic BaseSettings)
- [x] Domain models (Claim, Member, Document, etc.)
- [x] Error hierarchy (AI, Policy, Document, Pipeline errors)
- [x] AI provider abstraction (AIProvider ABC)
- [x] Gemini adapter (default provider, only file with `google-genai` imports)
- [x] Anthropic adapter (alternate provider, only file with `anthropic` imports)
- [x] Provider factory
- [x] BaseAgent (dependency injection)
- [x] Structured logging foundation
- [x] Database foundation (async SQLAlchemy)
- [x] Repository pattern
- [x] pytest configuration + initial tests
- [x] React + TypeScript frontend shell
- [x] API service abstraction (frontend)
- [x] Docker setup
- [x] Documentation

### ✅ Phase 1 — Complete
- [x] Trace domain models, `TraceService`, `TraceRepository`
- [x] `GET /api/v1/claims/{claim_id}/trace`
- [x] Reusable `TraceViewer` frontend component

### ✅ Phase 2A — Complete
- [x] `ClaimValidationAgent` — member, policy, category, minimum amount
- [x] `DocumentVerificationAgent` — required-document checks + real AI classification
- [x] `CrossDocumentValidationAgent` — patient-identity matching
- [x] `ClaimsPipeline` — orchestration, early stopping, graceful degradation on AI/infra failure
- [x] `POST /api/v1/claims`, `GET /api/v1/claims/{claim_id}`
- [x] Claim submission + detail pages (detail page mounts `TraceViewer`)
- [x] Evaluation runner — TC001/TC002/TC003 all pass through the real pipeline (`scripts/run_eval.py`)
- [ ] Document extraction agent (OCR) — classification only so far, no full extraction
- [ ] Policy evaluation engine, financial calculation, fraud analysis, decision generation, explanation agent

### 🔲 Phase 2B / 3 — Planned
- [ ] Policy evaluation engine (coverage, waiting periods, exclusions, co-pay, network discount)
- [ ] Financial calculation service
- [ ] Fraud analysis agent
- [ ] Decision generation + explanation agent
- [ ] Remaining 9 test cases (TC004–TC012) + `eval-report.md`
- [ ] Real file upload + multimodal document understanding
- [ ] Deployment

---

## Assignment Files

| File | Purpose |
|------|---------|
| `policy_terms.json` | Policy rules, limits, exclusions, members. **Source of truth.** |
| `test_cases.json` | 12 evaluation test cases with expected decisions |
| `sample_documents_guide.md` | Document types, formats, extraction requirements |
