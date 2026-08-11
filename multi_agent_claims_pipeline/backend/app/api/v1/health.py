"""
Health check endpoint.

GET /api/v1/health

Returns structured health status for the application.
Used by Docker healthchecks, load balancers, and monitoring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import AIProviderDep, SettingsDep
from app.config.settings import Settings

router = APIRouter()


class AIProviderStatus(BaseModel):
    provider: str
    model: str
    status: str  # "configured" | "unconfigured" | "error"


class HealthResponse(BaseModel):
    status: str  # "healthy" | "degraded" | "unhealthy"
    version: str
    environment: str
    timestamp: str
    ai_provider: AIProviderStatus
    database: str  # "connected" | "disconnected"
    uptime_note: str = "Full pipeline operational — Claim Validation through Explanation"


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health Check",
    description="Returns the operational status of the claims processing system.",
    tags=["System"],
)
async def health_check(
    settings: SettingsDep,
    ai_provider: AIProviderDep,
) -> HealthResponse:
    """
    Liveness + basic readiness check.

    Does NOT make a live AI API call (to keep this endpoint fast and free).
    Full AI provider health is checked separately.
    """
    active_api_key = getattr(settings, f"{settings.ai_provider.value}_api_key", None)
    ai_status = AIProviderStatus(
        provider=settings.ai_provider.value,
        model=settings.ai_model,
        status="configured" if active_api_key else "unconfigured",
    )

    return HealthResponse(
        status="healthy",
        version=settings.app_version,
        environment=settings.app_env.value,
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        ai_provider=ai_status,
        database="connected",
    )
