"""
Database foundation.

Phase 0: Establishes the async SQLAlchemy engine and session factory.
All claim tables will be added in Phase 1.

Design:
- Uses aiosqlite for async SQLite (dev/testing).
- DATABASE_URL can be swapped to PostgreSQL (asyncpg) without changing any
  other code — only the URL env var needs to change.
- All DB access goes through repositories, never directly from agents.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.tracing.logging import get_logger

logger = get_logger("Database")


class Base(DeclarativeBase):
    """
    SQLAlchemy declarative base.
    All ORM models (added in Phase 1+) must inherit from this.
    """
    pass


# Module-level singletons — initialised by the app lifespan
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_database(database_url: str) -> None:
    """
    Initialise the database engine and create tables.
    Called once at application startup (in lifespan).

    Args:
        database_url: SQLAlchemy async URL, e.g.:
            sqlite+aiosqlite:///./data/claims.db
            postgresql+asyncpg://user:password@host/dbname
    """
    global _engine, _session_factory

    logger.info("Initialising database", url=database_url.split("@")[-1])  # Mask credentials

    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _engine = create_async_engine(
        database_url,
        echo=False,  # Set to True for SQL query logging (dev only)
        connect_args=connect_args,
    )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Import ORM modules so their classes register with Base.metadata before
    # create_all runs. Declarative registration happens at class-definition
    # time, so each ORM module must be imported at least once before this call.
    from app.repositories import claim_models, trace_models  # noqa: F401

    # Create all tables defined in ORM models
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialised successfully")


async def close_database() -> None:
    """Dispose the engine and release connections. Called at app shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        logger.info("Database connection closed")
        _engine = None


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager that yields a database session.
    Rolls back on exception, commits on success, always closes.

    Usage:
        async with get_session() as session:
            result = await session.execute(...)
    """
    if _session_factory is None:
        raise RuntimeError("Database not initialised. Call init_database() first.")

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
