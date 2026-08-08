"""
FastAPI application factory.

This is the entry point for the backend.

Phase 0: Health endpoint only.
Phase 1+: Claims processing endpoints, WebSocket trace streaming, etc.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.config.settings import get_settings
from app.domain.errors import ClaimsSystemError
from app.repositories.database import close_database, init_database
from app.tracing.logging import configure_logging, get_logger

settings = get_settings()
logger = get_logger("Application")


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan context manager.
    Runs startup logic before yield, teardown logic after.
    """
    # ── Startup ───────────────────────────────────────────────────────────────
    configure_logging(level=settings.log_level.value, json_mode=settings.log_json)
    logger.info(
        f"Starting {settings.app_name} v{settings.app_version}",
        environment=settings.app_env.value,
    )

    # Ensure data directory exists
    os.makedirs("data", exist_ok=True)

    # Initialise database
    await init_database(settings.database_url)

    # Initialise AI provider
    from app.api.deps import get_ai_provider

    ai_provider = get_ai_provider()
    try:
        await ai_provider.initialize()
        logger.info(
            f"AI provider '{ai_provider.provider_name}' initialised",
            model=ai_provider.model_name,
        )
    except Exception as exc:
        # Non-fatal: app still starts but AI calls will fail
        logger.error(
            f"AI provider failed to initialise: {exc}. "
            "The application will start but AI operations will fail."
        )

    logger.info("Application startup complete — ready to serve requests")
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Shutting down application...")

    try:
        await ai_provider.close()
    except Exception:
        pass

    await close_database()
    logger.info("Application shutdown complete")


# ── App Factory ───────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI app instance.
    """
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Health Insurance Claims Processing System — "
            "Multi-agent AI pipeline for claim evaluation."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Global Exception Handlers ─────────────────────────────────────────────

    @app.exception_handler(ClaimsSystemError)
    async def claims_error_handler(
        request: Request, exc: ClaimsSystemError
    ) -> JSONResponse:
        """Convert domain errors into structured API error responses."""
        logger.error(
            f"Domain error: {exc.code} — {exc.message}",
            error_code=exc.code,
            recoverable=exc.recoverable,
        )
        status_code = 422 if exc.recoverable else 500
        return JSONResponse(
            status_code=status_code,
            content={
                "error": exc.code,
                "message": exc.message,
                "details": exc.details,
                "recoverable": exc.recoverable,
            },
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Catch-all for unexpected errors — never expose internals."""
        logger.exception(f"Unhandled exception: {type(exc).__name__}: {exc}")
        return JSONResponse(
            status_code=500,
            content={
                "error": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred. Please try again.",
            },
        )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(v1_router)

    # Root redirect (dev convenience)
    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "health": "/api/v1/health",
        }

    return app


# ── Entry Point ───────────────────────────────────────────────────────────────

app = create_app()
