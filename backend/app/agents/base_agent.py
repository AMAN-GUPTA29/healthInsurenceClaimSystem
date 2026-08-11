"""
Base Agent.

All pipeline agents inherit from BaseAgent.

Rules:
- Agents receive an AIProvider via __init__ (dependency injection) — or
  None, for agents that are purely deterministic and never call AI (e.g.
  ClaimValidationAgent, CrossDocumentValidationAgent in Phase 2A).
- Agents NEVER import AI vendor SDKs.
- Agents operate on domain models (Claim, Document, etc.).
- Agents return domain models or raise domain errors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from app.ai.providers.base import AIProvider
from app.domain.errors import AIProviderNotConfiguredError
from app.tracing.logging import get_logger


class BaseAgent(ABC):
    """
    Abstract base for all claims pipeline agents.

    Agents are single-responsibility components that perform one step
    in the pipeline (e.g., document verification, policy evaluation).

    Lifecycle:
        agent = ConcreteAgent(ai_provider=provider, ...)
        result = await agent.run(input_data)
    """

    def __init__(
        self,
        *,
        ai_provider: Optional[AIProvider] = None,
        agent_name: Optional[str] = None,
    ) -> None:
        self._ai_provider = ai_provider
        self.agent_name = agent_name or self.__class__.__name__
        self._logger = get_logger(component=self.agent_name)

    @abstractmethod
    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the agent's primary operation."""
        ...

    @property
    def ai_provider(self) -> AIProvider:
        """
        The injected AI provider. Read-only access for subclasses.
        Raises AIProviderNotConfiguredError if this agent was constructed
        without one — use `has_ai_provider` to check first if a subclass
        can legitimately run both with and without AI.
        """
        if self._ai_provider is None:
            raise AIProviderNotConfiguredError(self.agent_name)
        return self._ai_provider

    @property
    def has_ai_provider(self) -> bool:
        return self._ai_provider is not None
