"""
Async SQLAlchemy engine and session factory.

Connects to PostgreSQL 15 via Cloud SQL Auth Proxy (localhost:5432).
Never import engine/SessionLocal at module level in routes — always
use the get_db() FastAPI dependency so sessions are properly closed.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_pre_ping=True,  # validates connections before use, handles Cloud SQL restarts
    echo=settings.debug,  # SQL query logging in debug mode only
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # avoids lazy-load errors after commit in async context
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Shared declarative base — all ORM models inherit from this."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a database session and guarantees cleanup.

    Usage:
        @router.get("/accounts/{id}")
        async def get_account(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
