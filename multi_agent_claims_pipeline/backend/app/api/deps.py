"""
FastAPI Dependency Injection.

This module provides FastAPI-compatible dependency functions that construct
and inject shared resources into route handlers.

The AI provider is a singleton constructed from settings and shared
across all request handlers within a process. Agents receive it via
constructor injection, never by importing a module-level client.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.agents.claim_validation_agent import ClaimValidationAgent
from app.agents.cross_document_validation_agent import CrossDocumentValidationAgent
from app.agents.document_verification_agent import DocumentVerificationAgent
from app.ai.providers.base import AIProvider
from app.ai.providers.factory import create_ai_provider
from app.config.settings import Settings, get_settings
from app.pipeline.pipeline import ClaimsPipeline
from app.policy.policy_repository import PolicyRepository
from app.repositories.claim_repository import ClaimRepository
from app.repositories.trace_repository import TraceRepository
from app.services.document_input_adapter import DocumentInputAdapter


# ── Settings ──────────────────────────────────────────────────────────────────


def get_settings_dep() -> Settings:
    """FastAPI dependency that returns the application settings singleton."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


# ── AI Provider ───────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _get_ai_provider_singleton() -> AIProvider:
    """
    Create and cache the AI provider singleton.
    The provider is initialised (async) during app lifespan, not here.
    """
    return create_ai_provider(get_settings())


def get_ai_provider() -> AIProvider:
    """
    FastAPI dependency that returns the configured AI provider.
    Must be initialised before use (done in app lifespan).
    """
    return _get_ai_provider_singleton()


AIProviderDep = Annotated[AIProvider, Depends(get_ai_provider)]


# ── Trace Repository ──────────────────────────────────────────────────────────


def get_trace_repository() -> TraceRepository:
    """
    FastAPI dependency that returns a TraceRepository.

    Not cached/singleton: the repository is stateless — each method opens
    its own database session — so there's no shared state to preserve
    across requests.
    """
    return TraceRepository()


TraceRepositoryDep = Annotated[TraceRepository, Depends(get_trace_repository)]


# ── Claim Foundation (Phase 2A) ─────────────────────────────────────────────


@lru_cache(maxsize=1)
def _get_policy_repository_singleton() -> PolicyRepository:
    """
    Cached singleton: policy_terms.json is read once and reused, mirroring
    the AI provider / settings singleton pattern above.
    """
    return PolicyRepository(get_settings().policy_file_path)


def get_policy_repository() -> PolicyRepository:
    return _get_policy_repository_singleton()


PolicyRepositoryDep = Annotated[PolicyRepository, Depends(get_policy_repository)]


def get_claim_repository() -> ClaimRepository:
    """Not cached — same reasoning as get_trace_repository (stateless)."""
    return ClaimRepository()


ClaimRepositoryDep = Annotated[ClaimRepository, Depends(get_claim_repository)]


def get_document_input_adapter() -> DocumentInputAdapter:
    return DocumentInputAdapter()


DocumentInputAdapterDep = Annotated[DocumentInputAdapter, Depends(get_document_input_adapter)]


def get_claims_pipeline(
    ai_provider: AIProviderDep,
    policy_repository: PolicyRepositoryDep,
) -> ClaimsPipeline:
    """
    Builds a fresh ClaimsPipeline per request. Agents are cheap, stateless
    objects (all real state — the AI provider, the loaded policy — is
    injected, not owned), so there's no benefit to caching the pipeline
    itself the way the AI provider/policy repository are cached.
    """
    return ClaimsPipeline(
        claim_validation_agent=ClaimValidationAgent(policy_repository=policy_repository),
        document_verification_agent=DocumentVerificationAgent(
            ai_provider=ai_provider, policy_repository=policy_repository
        ),
        cross_document_validation_agent=CrossDocumentValidationAgent(),
    )


ClaimsPipelineDep = Annotated[ClaimsPipeline, Depends(get_claims_pipeline)]
