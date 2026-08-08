"""
Google Gemini Provider.

This is the ONLY file in the entire application that imports the Google
Gen AI SDK (`google-genai`).

Design:
- Implements AIProvider using the official `google-genai` Python SDK.
- All SDK types and errors are translated to domain types before returning.
- No vendor types leak across the provider boundary.
- Supports text generation, structured JSON generation (via response_schema),
  and document/image analysis (via inline multimodal parts).

Supported models:
  gemini-2.5-flash, gemini-2.5-pro, gemini-2.0-flash, etc.
  (configured via AI_MODEL env var)
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, List, Optional, Union

# ── ONLY PLACE IN THE APP WHERE THE GOOGLE GENAI SDK IS IMPORTED ─────────────
try:
    from google import genai
    from google.genai import types as genai_types
    from google.genai import errors as genai_errors
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    genai = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]
    genai_errors = None  # type: ignore[assignment]
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


class GeminiProvider(AIProvider):
    """
    Google Gemini adapter.

    Receives configuration via constructor — no global state, no module-level
    clients. Use the factory in app.ai.providers.factory to get a configured
    instance.
    """

    PROVIDER_NAME = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout_seconds: int = 60,
    ) -> None:
        if not GENAI_AVAILABLE:
            raise AIProviderNotConfiguredError(self.PROVIDER_NAME)
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds
        self._client: Optional["genai.Client"] = None  # type: ignore[name-defined]

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create the Gemini client. Called once at app startup."""
        if not self._api_key:
            raise AIAuthenticationError(self.PROVIDER_NAME)
        self._client = genai.Client(
            api_key=self._api_key,
            http_options=genai_types.HttpOptions(
                timeout=int(self._timeout_seconds * 1000)  # ms
            ),
        )

    async def close(self) -> None:
        """Release the client reference (google-genai has no explicit close)."""
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
        # Gemini accepts PDF bytes directly as an inline multimodal part.
        return True

    @property
    def _effective_client(self) -> "genai.Client":  # type: ignore[name-defined]
        if self._client is None:
            raise AIProviderError(
                "GeminiProvider has not been initialised. Call initialize() first.",
                provider=self.PROVIDER_NAME,
            )
        return self._client

    # ── Message Translation ───────────────────────────────────────────────────

    def _translate_parts(self, content: Union[str, List[Any]]) -> List[Any]:
        """Translate message content into Gemini `Part` objects."""
        if isinstance(content, str):
            return [genai_types.Part.from_text(text=content)]
        parts: List[Any] = []
        for item in content:
            if isinstance(item, TextContent):
                parts.append(genai_types.Part.from_text(text=item.text))
            elif isinstance(item, ImageContent):
                if item.data:
                    parts.append(
                        genai_types.Part.from_bytes(
                            data=base64.b64decode(item.data),
                            mime_type=item.media_type.value,
                        )
                    )
                elif item.url:
                    parts.append(
                        genai_types.Part.from_uri(
                            file_uri=item.url, mime_type=item.media_type.value
                        )
                    )
            elif isinstance(item, DocumentContent):
                parts.append(
                    genai_types.Part.from_bytes(
                        data=base64.b64decode(item.data),
                        mime_type=item.media_type.value,
                    )
                )
        return parts

    def _translate_messages(self, messages: List[Message]) -> List[Any]:
        """Convert vendor-agnostic Message objects into Gemini `Content` objects."""
        contents: List[Any] = []
        for msg in messages:
            if msg.role == ContentRole.SYSTEM:
                # Gemini takes the system prompt as a separate config field.
                continue
            role = "model" if msg.role == ContentRole.ASSISTANT else "user"
            contents.append(
                genai_types.Content(role=role, parts=self._translate_parts(msg.content))
            )
        return contents

    def _extract_system_prompt(self, messages: List[Message]) -> Optional[str]:
        """Extract system prompt from messages list (Gemini takes it separately)."""
        for msg in messages:
            if msg.role == ContentRole.SYSTEM:
                if isinstance(msg.content, str):
                    return msg.content
                return " ".join(
                    item.text for item in msg.content if isinstance(item, TextContent)
                )
        return None

    def _parse_usage(self, usage_meta: Any) -> TokenUsage:
        """Parse Gemini usage_metadata object into our TokenUsage schema."""
        if usage_meta is None:
            return TokenUsage()
        return TokenUsage(
            input_tokens=getattr(usage_meta, "prompt_token_count", None),
            output_tokens=getattr(usage_meta, "candidates_token_count", None),
            total_tokens=getattr(usage_meta, "total_token_count", None),
        )

    def _finish_reason(self, response: Any) -> Optional[str]:
        candidates = getattr(response, "candidates", None)
        if not candidates:
            return None
        reason = getattr(candidates[0], "finish_reason", None)
        return str(reason) if reason is not None else None

    def _wrap_error(self, exc: Exception) -> AIProviderError:
        """Translate google-genai SDK exceptions into domain errors."""
        if genai_errors is not None and isinstance(
            exc, (genai_errors.ClientError, genai_errors.ServerError)
        ):
            status_code = getattr(exc, "code", None)
            if status_code in (401, 403):
                return AIAuthenticationError(self.PROVIDER_NAME)
            if status_code == 429:
                return AIRateLimitError(self.PROVIDER_NAME)
            return AIProviderError(
                f"Gemini API error {status_code}: {exc}",
                provider=self.PROVIDER_NAME,
                model=self._model,
            )
        if isinstance(exc, TimeoutError):
            return AITimeoutError(self.PROVIDER_NAME, self._timeout_seconds)
        return AIProviderError(
            f"Unexpected error from Gemini provider: {exc}",
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
        system = request.system_prompt or self._extract_system_prompt(request.messages)
        contents = self._translate_messages(request.messages)

        config = genai_types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system,
        )

        t0 = time.monotonic()
        try:
            response = await self._effective_client.aio.models.generate_content(
                model=model, contents=contents, config=config,
            )
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        latency_ms = (time.monotonic() - t0) * 1000

        return AIGenerateResponse(
            text=response.text or "",
            model=model,
            provider=self.PROVIDER_NAME,
            usage=self._parse_usage(getattr(response, "usage_metadata", None)),
            finish_reason=self._finish_reason(response),
            latency_ms=latency_ms,
        )

    # ── Structured Generation ─────────────────────────────────────────────────

    async def generate_structured(
        self,
        request: AIStructuredRequest,
    ) -> AIStructuredResponse:
        """
        Use Gemini's `response_schema` / `response_mime_type=application/json`
        controlled-generation feature to guarantee structured JSON output.
        """
        model = request.model or self._model
        temperature = request.temperature if request.temperature is not None else self._temperature
        max_tokens = request.max_tokens or self._max_tokens
        system = request.system_prompt or self._extract_system_prompt(request.messages)
        contents = self._translate_messages(request.messages)

        config = genai_types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=request.output_schema,
        )

        t0 = time.monotonic()
        try:
            response = await self._effective_client.aio.models.generate_content(
                model=model, contents=contents, config=config,
            )
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        latency_ms = (time.monotonic() - t0) * 1000

        raw_text = response.text
        try:
            structured_data = json.loads(raw_text) if raw_text else {}
        except json.JSONDecodeError as exc:
            raise AIResponseParseError(
                self.PROVIDER_NAME, f"Response was not valid JSON: {exc}"
            ) from exc

        if not structured_data:
            raise AIResponseParseError(
                self.PROVIDER_NAME, "Empty structured response from Gemini."
            )

        return AIStructuredResponse(
            data=structured_data,
            raw_text=raw_text,
            model=model,
            provider=self.PROVIDER_NAME,
            usage=self._parse_usage(getattr(response, "usage_metadata", None)),
            finish_reason=self._finish_reason(response),
            latency_ms=latency_ms,
        )

    # ── Document Analysis ─────────────────────────────────────────────────────

    async def analyze_document(
        self,
        request: DocumentAnalysisRequest,
    ) -> DocumentAnalysisResponse:
        """
        Analyse documents (images / PDFs) using Gemini's native multimodal input.
        If output_schema is provided, forces structured JSON output.
        """
        parts: List[Any] = []
        for doc in request.documents:
            if isinstance(doc, ImageContent):
                if doc.data:
                    parts.append(
                        genai_types.Part.from_bytes(
                            data=base64.b64decode(doc.data),
                            mime_type=doc.media_type.value,
                        )
                    )
                elif doc.url:
                    parts.append(
                        genai_types.Part.from_uri(
                            file_uri=doc.url, mime_type=doc.media_type.value
                        )
                    )
            elif isinstance(doc, DocumentContent):
                parts.append(
                    genai_types.Part.from_bytes(
                        data=base64.b64decode(doc.data),
                        mime_type=doc.media_type.value,
                    )
                )

        parts.append(genai_types.Part.from_text(text=request.prompt))
        contents = [genai_types.Content(role="user", parts=parts)]

        model = request.model or self._model
        temperature = request.temperature if request.temperature is not None else self._temperature
        max_tokens = request.max_tokens or self._max_tokens

        config_kwargs: Dict[str, Any] = dict(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        if request.system_prompt:
            config_kwargs["system_instruction"] = request.system_prompt
        if request.output_schema:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = request.output_schema

        config = genai_types.GenerateContentConfig(**config_kwargs)

        t0 = time.monotonic()
        try:
            response = await self._effective_client.aio.models.generate_content(
                model=model, contents=contents, config=config,
            )
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        latency_ms = (time.monotonic() - t0) * 1000

        text_out: Optional[str] = None
        structured_out: Optional[Dict[str, Any]] = None

        if request.output_schema:
            try:
                structured_out = json.loads(response.text) if response.text else {}
            except json.JSONDecodeError as exc:
                raise AIResponseParseError(
                    self.PROVIDER_NAME, f"Response was not valid JSON: {exc}"
                ) from exc
        else:
            text_out = response.text

        return DocumentAnalysisResponse(
            text=text_out,
            structured_data=structured_out,
            model=model,
            provider=self.PROVIDER_NAME,
            usage=self._parse_usage(getattr(response, "usage_metadata", None)),
            finish_reason=self._finish_reason(response),
            latency_ms=latency_ms,
            file_ids=request.file_ids,
        )
