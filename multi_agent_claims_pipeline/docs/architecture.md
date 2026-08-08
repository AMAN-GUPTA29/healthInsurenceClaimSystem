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

---

## Component Map

### Backend Layers

```
┌────────────────────────────────────────────────────────────────┐
│  API Layer (FastAPI)                                           │
│  app/api/v1/health.py   ← GET /api/v1/health                 │
│  app/api/deps.py        ← Dependency injection                │
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  Pipeline Layer (Phase 1)                                      │
│  app/pipeline/pipeline.py  ← Multi-agent orchestrator         │
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  Agent Layer (Phase 1)                                         │
│  app/agents/base_agent.py   ← BaseAgent(ai_provider=...)      │
│  app/agents/validation_agent.py   ← (planned)                 │
│  app/agents/extraction_agent.py   ← (planned)                 │
│  ...                                                           │
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  AI Layer                                                      │
│  app/ai/providers/base.py            ← AIProvider ABC         │
│  app/ai/providers/anthropic_provider.py  ← Anthropic adapter  │
│  app/ai/schemas/ai_schemas.py        ← Request/response types │
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  Domain Layer                                                  │
│  app/domain/models.py   ← Claim, Member, Document, Decision   │
│  app/domain/errors.py   ← Error hierarchy                     │
└────────────────────┬───────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────────┐
│  Infrastructure Layer                                          │
│  app/repositories/database.py  ← Async SQLAlchemy             │
│  app/repositories/base.py      ← Repository ABC               │
│  app/tracing/logging.py        ← Structured logging           │
│  app/config/settings.py        ← Pydantic BaseSettings        │
└────────────────────────────────────────────────────────────────┘
```

---

## Database Design

**Phase 0**: SQLite via aiosqlite (development default)  
**Phase 1+**: Add ORM models for:
- `claims` table
- `documents` table
- `extractions` table
- `decisions` table
- `trace_events` table (claim-level observability)

**Migration path**: Changing `DATABASE_URL` to a PostgreSQL `asyncpg://` URL requires no code changes. The SQLAlchemy ORM is database-agnostic.

---

## AI Provider Interface

The `AIProvider` ABC exposes three core capabilities:

| Method | Use Case | Output |
|--------|----------|--------|
| `generate_text()` | Explanations, member messages | `AIGenerateResponse` |
| `generate_structured()` | Extraction, classification | `AIStructuredResponse` |
| `analyze_document()` | OCR, document type detection | `DocumentAnalysisResponse` |

Structured output uses Anthropic's `tool_use` feature to guarantee JSON schema conformance.

---

## Frontend Architecture

```
src/
├── App.tsx              ← Router + layout shell
├── main.tsx             ← React entry point
├── types/index.ts       ← TypeScript types (mirrors backend models)
├── services/api.ts      ← API client abstraction
├── hooks/useHealth.ts   ← Data-fetching hook
└── pages/Dashboard.tsx  ← System health dashboard
```

All backend calls go through `services/api.ts`. Components never call `fetch()` directly.

---

## Configuration Architecture

Settings are loaded once via `get_settings()` (lru_cache singleton):

```python
APP_ENV → Environment enum
AI_PROVIDER → AIProvider enum → Provider factory → Concrete provider
ANTHROPIC_API_KEY → Injected into AnthropicProvider (never hardcoded)
DATABASE_URL → SQLAlchemy engine (swappable)
```

Per-agent model overrides are supported by passing `model` in `AIGenerateRequest` — no routing infrastructure needed yet.

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
         ▼
Pipeline Orchestrator (Phase 1)
    if recoverable:
        log + skip component + lower confidence
    else:
        stop pipeline + return error response
         │
         ▼
API Exception Handler (app/main.py)
    → Structured JSON error response
```
