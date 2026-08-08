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

## Planned Phase 1 Contracts (stubs)

These components will receive full contracts when implemented:

### ClaimsPipeline
- **Input**: `ClaimSubmission` + `AIProvider`
- **Output**: `ClaimDecision`
- **Guarantee**: Never raises; always returns a decision (may be `MANUAL_REVIEW` on failures)

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
