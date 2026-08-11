"""
Anthropic Claude Provider.

This is the ONLY file in the entire application that imports the Anthropic SDK.

Design:
- Implements AIProvider using the official `anthropic` Python SDK.
- All SDK types and errors are translated to domain types before returning.
- No vendor types leak across the provider boundary.
- Supports text generation, structured JSON generation (via tool_use),
  and document/image analysis (via vision messages).

Supported models:
  claude-opus-4-5, claude-sonnet-4-5, claude-haiku-3-5, etc.
  (configured via AI_MODEL env var)
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, List, Optional, Union

# ── ONLY PLACE IN THE APP WHERE ANTHROPIC SDK IS IMPORTED ────────────────────
try:
    import anthropic
    from anthropic import AsyncAnthropic, APIConnectionError, APIStatusError, APITimeoutError
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    anthropic = None  # type: ignore[assignment]
    AsyncAnthropic = None  # type: ignore[assignment]
# ─────────────────────────────────────────────────────────────────────────────

from app.ai.providers.base import AIProvider
from app.ai.schemas.ai_schemas import (
    AIGenerateRequest,
    AIGenerateResponse,
    AIStructuredRequest,
    AIStructuredResponse,
    ContentRole,
    DocumentAnalysisRequest,
    DocumentAnalysisResponse,
    DocumentContent,
    ImageContent,
    MediaType,
    Message,
    TextContent,
    TokenUsage,
)
from app.domain.errors import (
    AIAuthenticationError,
    AIProviderError,
    AIProviderNotConfiguredError,
    AIRateLimitError,
    AIResponseParseError,
    AITimeoutError,
)


class AnthropicProvider(AIProvider):
    """
    Anthropic Claude adapter.

    Receives configuration via constructor — no global state, no module-level
    clients. Use the factory in app.api.deps to get a configured instance.
    """

    PROVIDER_NAME = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout_seconds: int = 60,
    ) -> None:
        if not ANTHROPIC_AVAILABLE:
            raise AIProviderNotConfiguredError(
                self.PROVIDER_NAME,
            )
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds
        self._client: Optional[AsyncAnthropic] = None  # type: ignore[valid-type]

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create the async Anthropic client. Called once at app startup."""
        if not self._api_key:
            raise AIAuthenticationError(self.PROVIDER_NAME)
        self._client = AsyncAnthropic(
            api_key=self._api_key,
            timeout=float(self._timeout_seconds),
        )

    async def close(self) -> None:
        """Close underlying HTTP connections."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def supports_native_pdf(self) -> bool:
        # Claude 3+ models support PDF via the document block (beta as of 2024).
        return True

    @property
    def _effective_client(self) -> "AsyncAnthropic":  # type: ignore[return]
        if self._client is None:
            raise AIProviderError(
                "AnthropicProvider has not been initialised. Call initialize() first.",
                provider=self.PROVIDER_NAME,
            )
        return self._client

    # ── Message Translation ───────────────────────────────────────────────────

    def _translate_messages(
        self, messages: List[Message]
    ) -> List[Dict[str, Any]]:
        """Convert our vendor-agnostic Message objects into Anthropic API message dicts."""
        anthropic_messages: List[Dict[str, Any]] = []
        for msg in messages:
            if msg.role == ContentRole.SYSTEM:
                # Anthropic handles system prompts as a top-level param, not a message.
                continue
            content = self._translate_content(msg.content)
            anthropic_messages.append({"role": msg.role.value, "content": content})
        return anthropic_messages

    def _translate_content(
        self, content: Union[str, List[Any]]
    ) -> Union[str, List[Dict[str, Any]]]:
        """Translate message content to Anthropic format."""
        if isinstance(content, str):
            return content
        blocks: List[Dict[str, Any]] = []
        for item in content:
            if isinstance(item, TextContent):
                blocks.append({"type": "text", "text": item.text})
            elif isinstance(item, ImageContent):
                if item.data:
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": item.media_type.value,
                                "data": item.data,
                            },
                        }
                    )
                elif item.url:
                    blocks.append(
                        {
                            "type": "image",
                            "source": {"type": "url", "url": item.url},
                        }
                    )
            elif isinstance(item, DocumentContent):
                # Claude's PDF support (beta)
                blocks.append(
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": item.media_type.value,
                            "data": item.data,
                        },
                    }
                )
        return blocks

    def _extract_system_prompt(self, messages: List[Message]) -> Optional[str]:
        """Extract system prompt from messages list (Anthropic takes it separately)."""
        for msg in messages:
            if msg.role == ContentRole.SYSTEM:
                if isinstance(msg.content, str):
                    return msg.content
                return " ".join(
                    item.text for item in msg.content if isinstance(item, TextContent)
                )
        return None

    def _parse_usage(self, usage_obj: Any) -> TokenUsage:
        """Parse Anthropic usage object into our TokenUsage schema."""
        return TokenUsage(
            input_tokens=getattr(usage_obj, "input_tokens", None),
            output_tokens=getattr(usage_obj, "output_tokens", None),
            total_tokens=(
                (getattr(usage_obj, "input_tokens", 0) or 0)
                + (getattr(usage_obj, "output_tokens", 0) or 0)
            ),
            cache_read_tokens=getattr(usage_obj, "cache_read_input_tokens", None),
            cache_write_tokens=getattr(usage_obj, "cache_creation_input_tokens", None),
        )

    def _wrap_error(self, exc: Exception) -> AIProviderError:
        """Translate Anthropic SDK exceptions into domain errors."""
        if isinstance(exc, APITimeoutError):
            return AITimeoutError(self.PROVIDER_NAME, self._timeout_seconds)
        if isinstance(exc, APIStatusError):
            if exc.status_code == 401:
                return AIAuthenticationError(self.PROVIDER_NAME)
            if exc.status_code == 429:
                return AIRateLimitError(self.PROVIDER_NAME)
            return AIProviderError(
                f"Anthropic API error {exc.status_code}: {exc.message}",
                provider=self.PROVIDER_NAME,
                model=self._model,
            )
        if isinstance(exc, APIConnectionError):
            return AIProviderError(
                f"Could not connect to Anthropic API: {exc}",
                provider=self.PROVIDER_NAME,
            )
        return AIProviderError(
            f"Unexpected error from Anthropic provider: {exc}",
            provider=self.PROVIDER_NAME,
        )

    # ── Text Generation ───────────────────────────────────────────────────────

    async def generate_text(
        self,
        request: AIGenerateRequest,
    ) -> AIGenerateResponse:
        model = request.model or self._model
        temperature = request.temperature if request.temperature is not None else self._temperature
        max_tokens = request.max_tokens or self._max_tokens
        timeout = request.timeout_seconds or self._timeout_seconds

        system = request.system_prompt or self._extract_system_prompt(request.messages)
        messages = self._translate_messages(request.messages)

        kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        t0 = time.monotonic()
        try:
            response = await self._effective_client.messages.create(**kwargs)
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        latency_ms = (time.monotonic() - t0) * 1000

        text = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )
        return AIGenerateResponse(
            text=text,
            model=response.model,
            provider=self.PROVIDER_NAME,
            usage=self._parse_usage(response.usage),
            finish_reason=response.stop_reason,
            latency_ms=latency_ms,
        )

    # ── Structured Generation ─────────────────────────────────────────────────

    async def generate_structured(
        self,
        request: AIStructuredRequest,
    ) -> AIStructuredResponse:
        """
        Use Anthropic's tool_use feature to guarantee structured JSON output.
        We define a single tool whose input_schema is the caller's output_schema.
        Claude is forced to call this tool, giving us parsed JSON back.
        """
        model = request.model or self._model
        temperature = request.temperature if request.temperature is not None else self._temperature
        max_tokens = request.max_tokens or self._max_tokens

        system = request.system_prompt or self._extract_system_prompt(request.messages)
        messages = self._translate_messages(request.messages)

        tool_name = request.schema_name.replace(" ", "_").lower()
        tools = [
            {
                "name": tool_name,
                "description": f"Return a structured response matching the {tool_name} schema.",
                "input_schema": request.output_schema,
            }
        ]

        kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
            "tools": tools,
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        if system:
            kwargs["system"] = system

        t0 = time.monotonic()
        try:
            response = await self._effective_client.messages.create(**kwargs)
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        latency_ms = (time.monotonic() - t0) * 1000

        # Extract the tool_use block
        raw_text: Optional[str] = None
        structured_data: Dict[str, Any] = {}
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                structured_data = block.input  # Already parsed dict from SDK
                break
            if hasattr(block, "text"):
                raw_text = block.text

        if not structured_data:
            raise AIResponseParseError(
                self.PROVIDER_NAME,
                "No tool_use block found in response. Cannot extract structured output.",
            )

        return AIStructuredResponse(
            data=structured_data,
            raw_text=raw_text,
            model=response.model,
            provider=self.PROVIDER_NAME,
            usage=self._parse_usage(response.usage),
            finish_reason=response.stop_reason,
            latency_ms=latency_ms,
        )

    # ── Document Analysis ─────────────────────────────────────────────────────

    async def analyze_document(
        self,
        request: DocumentAnalysisRequest,
    ) -> DocumentAnalysisResponse:
        """
        Analyse documents (images / PDFs) using Claude's vision capability.
        If output_schema is provided, forces structured output via tool_use.
        """
        content_blocks: List[Dict[str, Any]] = []

        # Add document blocks
        for doc in request.documents:
            if isinstance(doc, ImageContent):
                if doc.data:
                    content_blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": doc.media_type.value,
                                "data": doc.data,
                            },
                        }
                    )
                elif doc.url:
                    content_blocks.append(
                        {"type": "image", "source": {"type": "url", "url": doc.url}}
                    )
            elif isinstance(doc, DocumentContent):
                content_blocks.append(
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": doc.media_type.value,
                            "data": doc.data,
                        },
                    }
                )

        # Add the text prompt
        content_blocks.append({"type": "text", "text": request.prompt})

        messages = [{"role": "user", "content": content_blocks}]
        model = request.model or self._model
        temperature = request.temperature if request.temperature is not None else self._temperature
        max_tokens = request.max_tokens or self._max_tokens

        kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if request.system_prompt:
            kwargs["system"] = request.system_prompt

        # Structured output mode
        if request.output_schema:
            kwargs["tools"] = [
                {
                    "name": "document_extraction",
                    "description": "Extract structured data from the document.",
                    "input_schema": request.output_schema,
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": "document_extraction"}

        t0 = time.monotonic()
        try:
            response = await self._effective_client.messages.create(**kwargs)
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        latency_ms = (time.monotonic() - t0) * 1000

        text_out: Optional[str] = None
        structured_out: Optional[Dict[str, Any]] = None

        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                structured_out = block.input
            elif hasattr(block, "text"):
                text_out = block.text

        return DocumentAnalysisResponse(
            text=text_out,
            structured_data=structured_out,
            model=response.model,
            provider=self.PROVIDER_NAME,
            usage=self._parse_usage(response.usage),
            finish_reason=response.stop_reason,
            latency_ms=latency_ms,
            file_ids=request.file_ids,
        )
