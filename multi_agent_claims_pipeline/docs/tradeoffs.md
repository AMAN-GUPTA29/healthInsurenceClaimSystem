# Trade-offs & Decisions

This document records significant trade-offs made during the project.

---

## Phase 0

### SQLite vs PostgreSQL
**Choice**: SQLite (dev default) with PostgreSQL-ready architecture  
**Reason**: This is a 2-3 day assignment. SQLite requires zero infrastructure. The `DATABASE_URL` env var and SQLAlchemy async engine make switching to PostgreSQL a 1-line change.  
**Trade-off**: SQLite has WAL limitations under concurrent writes. If the evaluation runner needs concurrency, use PostgreSQL.

### ABC vs Protocol for AIProvider
**Choice**: `abc.ABC` with `@abstractmethod`  
**Reason**: Makes missing implementations fail loudly at class definition time, not call time. Protocol would be more duck-typing friendly but less explicit for this use case.  
**Trade-off**: Slightly more boilerplate for simple providers.

### Anthropic `tool_use` for structured output
**Choice**: Force structured JSON via `tool_use`, not by prompting for JSON  
**Reason**: `tool_use` is more reliable. Prompting for JSON leads to occasional malformed responses that need retry logic.  
**Trade-off**: Slightly more tokens used; requires Anthropic-specific logic in the adapter.

### Pydantic v2 with `BaseSettings`
**Choice**: Pydantic v2 + `pydantic-settings`  
**Reason**: Better performance, stricter validation, built-in env var support.  
**Trade-off**: `pydantic-settings` is a separate package; some v1 patterns don't apply.

### Frontend: Inline styles vs CSS modules
**Choice**: Inline styles for Phase 0 shell  
**Reason**: No build step needed to see results; zero dependency on CSS tooling.  
**Trade-off**: Not scalable for large UIs. Phase 1 will use CSS modules or a design system.
