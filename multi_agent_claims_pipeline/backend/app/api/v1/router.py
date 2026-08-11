"""API v1 router — aggregates all v1 endpoints."""

from fastapi import APIRouter

from app.api.v1 import claims, evaluation, health, traces

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(traces.router)
router.include_router(claims.router)
router.include_router(evaluation.router)
