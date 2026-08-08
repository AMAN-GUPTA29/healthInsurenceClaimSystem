"""API v1 router — aggregates all v1 endpoints."""

from fastapi import APIRouter

from app.api.v1 import health, traces

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(traces.router)
