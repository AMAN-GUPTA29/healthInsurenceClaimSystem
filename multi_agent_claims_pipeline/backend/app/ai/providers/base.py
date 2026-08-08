"""
AI Provider Abstract Base Class.

This is the ONLY interface that agents and pipeline components depend on.

Rules:
- No AI vendor SDK imports here.
- All methods are async.
- Errors must be domain errors (from app.domain.errors), never vendor exceptions.
- Providers translate vendor-specific errors into domain errors before raising.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.ai.schemas.ai_schemas import (
    AIGenerateRequest,
    AIGenerateResponse,
    AIStructuredRequest,
    AIStructuredResponse,
    DocumentAnalysisRequest,
    DocumentAnalysisResponse,
)


class AIProvider(ABC):
    """
    Abstract interface for all AI/LLM providers.

    Any new provider (Gemini, OpenAI, Cohere, etc.) must implement this
    interface. Business agents receive an AIProvider instance via dependency
    injection and must never import provider-specific code.

    Lifecycle:
        provider = AnthropicProvider(config)
        await provider.initialize()        # Validate auth, warm up if needed
        response = await provider.generate_text(request)
        await provider.close()             # Release connections
    """

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """
        Initialise the provider (validate API key, create client, etc.).
        Called once at application startup.
        Raises AIAuthenticationError if credentials are invalid.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any resources held by the provider (connection pools, etc.)."""
        ...

    # ── Core Capabilities ─────────────────────────────────────────────────────

    @abstractmethod
    async def generate_text(
        self,
        request: AIGenerateRequest,
    ) -> AIGenerateResponse:
        """
        Generate a free-form text response.

        Use for: explanations, member-facing messages, summaries.

        Args:
            request: The generation request (messages, system prompt, params).

        Returns:
            AIGenerateResponse with text and usage metadata.

        Raises:
            AITimeoutError: If the request exceeds timeout.
            AIRateLimitError: If the provider rate-limits the request.
            AIAuthenticationError: If authentication fails.
            AIProviderError: For other provider-side errors.
        """
        ...

    @abstractmethod
    async def generate_structured(
        self,
        request: AIStructuredRequest,
    ) -> AIStructuredResponse:
        """
        Generate a structured JSON response conforming to a schema.

        Use for: extraction, classification, policy-evaluation hints,
                 fraud signal detection, any step needing typed output.

        The provider must guarantee that `response.data` parses against
        the provided JSON Schema. If parsing fails, raise AIResponseParseError.

        Args:
            request: Includes messages, JSON schema, and optional model overrides.

        Returns:
            AIStructuredResponse with parsed dict and usage metadata.

        Raises:
            AIResponseParseError: If the output cannot be parsed to the schema.
            AITimeoutError, AIRateLimitError, AIProviderError: as above.
        """
        ...

    @abstractmethod
    async def analyze_document(
        self,
        request: DocumentAnalysisRequest,
    ) -> DocumentAnalysisResponse:
        """
        Analyse one or more document images or PDFs.

        Use for: document type detection, OCR extraction, quality assessment,
                 cross-document patient name matching.

        Args:
            request: Contains document bytes/URLs, analysis prompt, optional schema.

        Returns:
            DocumentAnalysisResponse with text and/or structured data.

        Raises:
            AITimeoutError, AIRateLimitError, AIProviderError: as above.
        """
        ...

    # ── Metadata ─────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Identifier string, e.g. 'anthropic', 'gemini', 'openai'."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The currently configured model identifier."""
        ...

    @property
    @abstractmethod
    def supports_native_pdf(self) -> bool:
        """True if this provider can process PDFs directly (vs. image conversion)."""
        ...

    @property
    def supports_vision(self) -> bool:
        """True if this provider supports image/vision inputs."""
        return True  # Default: most modern providers do

    # ── Optional: Health Check ────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """
        Lightweight check that the provider is reachable and authenticated.
        Subclasses may override for a more efficient implementation.
        Default: attempt a minimal generate_text call.
        """
        from app.ai.schemas.ai_schemas import ContentRole, Message, TextContent

        try:
            await self.generate_text(
                AIGenerateRequest(
                    messages=[
                        Message(
                            role=ContentRole.USER,
                            content=[TextContent(text="ping")],
                        )
                    ],
                    max_tokens=5,
                )
            )
            return True
        except Exception:
            return False
