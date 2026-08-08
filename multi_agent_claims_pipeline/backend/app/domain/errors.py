"""
Application error hierarchy.

Every error in the system derives from ClaimsSystemError so that
orchestrators/middleware can distinguish application errors from
unexpected exceptions.

Design goals:
- Errors carry enough context for the orchestrator to decide:
    * stop processing
    * continue with degraded confidence
    * request resubmission
    * route to MANUAL_REVIEW
- Never use bare `raise Exception(...)` in application code.
- Prefer specific subclasses.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


# ── Base ──────────────────────────────────────────────────────────────────────


class ClaimsSystemError(Exception):
    """
    Root of the application error hierarchy.
    All domain, AI, policy, and infrastructure errors derive from this.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "CLAIMS_SYSTEM_ERROR",
        details: Optional[Dict[str, Any]] = None,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}
        # recoverable = True means the pipeline can continue with degraded info
        self.recoverable = recoverable

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(code={self.code!r}, message={self.message!r})"


# ── Validation Errors ─────────────────────────────────────────────────────────


class ValidationError(ClaimsSystemError):
    """Raised when input data fails domain-level validation (not FastAPI validation)."""

    def __init__(self, message: str, *, field: Optional[str] = None, **kwargs: Any) -> None:
        kwargs.setdefault("code", "VALIDATION_ERROR")
        super().__init__(message, **kwargs)
        if field:
            self.details["field"] = field


class ClaimValidationError(ValidationError):
    """Raised when a submitted claim fails structural or business validation."""

    def __init__(self, message: str, *, claim_id: Optional[str] = None, **kwargs: Any) -> None:
        kwargs["code"] = "CLAIM_VALIDATION_ERROR"
        super().__init__(message, **kwargs)
        if claim_id:
            self.details["claim_id"] = claim_id


class MemberNotFoundError(ValidationError):
    """Raised when the member ID does not exist in the policy."""

    def __init__(self, member_id: str) -> None:
        super().__init__(
            f"Member '{member_id}' not found in the policy.",
            code="MEMBER_NOT_FOUND",  # sets via setdefault in ValidationError
        )
        self.details["member_id"] = member_id


class PolicyNotFoundError(ValidationError):
    """Raised when the referenced policy ID is not recognised."""

    def __init__(self, policy_id: str) -> None:
        super().__init__(
            f"Policy '{policy_id}' not found.",
            code="POLICY_NOT_FOUND",
        )
        self.details["policy_id"] = policy_id


# ── Document Errors ───────────────────────────────────────────────────────────


class DocumentError(ClaimsSystemError):
    """Base class for document-related errors."""

    def __init__(self, message: str, *, file_id: Optional[str] = None, **kwargs: Any) -> None:
        kwargs.setdefault("code", "DOCUMENT_ERROR")
        kwargs.setdefault("recoverable", True)
        super().__init__(message, **kwargs)
        if file_id:
            self.details["file_id"] = file_id


class DocumentUnreadableError(DocumentError):
    """Raised when a document cannot be parsed — too blurry, corrupt, etc."""

    def __init__(self, file_id: str, reason: str = "unreadable") -> None:
        super().__init__(
            f"Document '{file_id}' could not be read: {reason}.",
            file_id=file_id,
            code="DOCUMENT_UNREADABLE",  # overrides default via kwargs
        )


class DocumentTypeMismatchError(DocumentError):
    """Raised when a document's detected type doesn't match the expected type."""

    def __init__(self, file_id: str, expected: str, actual: str) -> None:
        super().__init__(
            f"Document '{file_id}': expected {expected}, got {actual}.",
            file_id=file_id,
            code="DOCUMENT_TYPE_MISMATCH",
        )
        self.details.update({"expected": expected, "actual": actual})


class DocumentPatientMismatchError(DocumentError):
    """Raised when documents in a claim belong to different patients."""

    def __init__(self, file_ids: list[str], patient_names: list[str]) -> None:
        super().__init__(
            "Documents in the claim belong to different patients.",
            code="DOCUMENT_PATIENT_MISMATCH",
        )
        self.details.update({"file_ids": file_ids, "patient_names": patient_names})


class MissingRequiredDocumentError(DocumentError):
    """Raised when required document types are absent for a claim category."""

    def __init__(self, category: str, missing_types: list[str]) -> None:
        super().__init__(
            f"Claim category '{category}' is missing required documents: {missing_types}.",
            code="MISSING_REQUIRED_DOCUMENT",
        )
        self.details.update({"category": category, "missing_types": missing_types})


class ExtractionError(DocumentError):
    """Raised when structured data cannot be extracted from a document."""

    def __init__(self, file_id: str, reason: str) -> None:
        super().__init__(
            f"Could not extract data from document '{file_id}': {reason}.",
            file_id=file_id,
            code="EXTRACTION_ERROR",
            recoverable=True,
        )


# ── AI / Provider Errors ──────────────────────────────────────────────────────


class AIProviderError(ClaimsSystemError):
    """Base class for AI provider errors."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "unknown",
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("code", "AI_PROVIDER_ERROR")
        kwargs.setdefault("recoverable", True)
        super().__init__(message, **kwargs)
        self.details.update({"provider": provider})
        if model:
            self.details["model"] = model


class AITimeoutError(AIProviderError):
    """Raised when an AI API call exceeds the configured timeout."""

    def __init__(self, provider: str, timeout_seconds: int) -> None:
        super().__init__(
            f"AI provider '{provider}' timed out after {timeout_seconds}s.",
            provider=provider,
            code="AI_TIMEOUT",
        )
        self.details["timeout_seconds"] = timeout_seconds


class AIRateLimitError(AIProviderError):
    """Raised when the AI API returns a rate-limit response."""

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"AI provider '{provider}' rate limit exceeded.",
            provider=provider,
            code="AI_RATE_LIMIT",
        )


class AIAuthenticationError(AIProviderError):
    """Raised when the API key is missing or invalid."""

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"AI provider '{provider}': authentication failed. Check API key.",
            provider=provider,
            code="AI_AUTH_ERROR",
            recoverable=False,
        )


class AIResponseParseError(AIProviderError):
    """Raised when the AI response cannot be parsed into the expected schema."""

    def __init__(self, provider: str, reason: str) -> None:
        super().__init__(
            f"Could not parse AI response from '{provider}': {reason}.",
            provider=provider,
            code="AI_RESPONSE_PARSE_ERROR",
        )


class AIProviderNotConfiguredError(AIProviderError):
    """Raised when the requested AI provider is not configured."""

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"AI provider '{provider}' is not configured or not implemented.",
            provider=provider,
            code="AI_PROVIDER_NOT_CONFIGURED",
            recoverable=False,
        )


# ── Policy Errors ─────────────────────────────────────────────────────────────


class PolicyError(ClaimsSystemError):
    """Base class for policy evaluation errors."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("code", "POLICY_ERROR")
        super().__init__(message, **kwargs)


class PolicyLoadError(PolicyError):
    """Raised when the policy file cannot be loaded or parsed."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(
            f"Could not load policy from '{path}': {reason}.",
            code="POLICY_LOAD_ERROR",
            recoverable=False,
        )
        self.details.update({"path": path, "reason": reason})


class PolicyEvaluationError(PolicyError):
    """Raised when policy evaluation encounters an unexpected error."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Policy evaluation failed: {reason}.",
            code="POLICY_EVALUATION_ERROR",
        )


# ── Pipeline / Component Errors ───────────────────────────────────────────────


class PipelineError(ClaimsSystemError):
    """Base class for pipeline orchestration errors."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("code", "PIPELINE_ERROR")
        super().__init__(message, **kwargs)


class ComponentFailureError(PipelineError):
    """
    Raised when a pipeline component (agent) fails.
    Marked recoverable so the orchestrator can skip the component,
    lower confidence, and continue.
    """

    def __init__(self, component_name: str, reason: str) -> None:
        super().__init__(
            f"Component '{component_name}' failed: {reason}.",
            code="COMPONENT_FAILURE",
            recoverable=True,  # setdefault in PipelineError, overridden here
        )
        self.details.update({"component": component_name, "reason": reason})


# ── Infrastructure Errors ─────────────────────────────────────────────────────


class InfrastructureError(ClaimsSystemError):
    """Base class for infrastructure-level errors (DB, network, filesystem)."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("code", "INFRASTRUCTURE_ERROR")
        kwargs.setdefault("recoverable", False)
        super().__init__(message, **kwargs)


class DatabaseError(InfrastructureError):
    """Raised when a database operation fails."""

    def __init__(self, operation: str, reason: str) -> None:
        super().__init__(
            f"Database operation '{operation}' failed: {reason}.",
            code="DATABASE_ERROR",
        )
        self.details.update({"operation": operation, "reason": reason})
