"""
Integration test fixtures.

Requires a real PostgreSQL reachable at the URL in TEST_DATABASE_URL env var.
If unset or unreachable, integration tests skip (so unit-test runs in CI without
a database aren't blocked).

Spin up a local test DB with:
    docker run --rm -p 5433:5432 -e POSTGRES_PASSWORD=test \
        -e POSTGRES_DB=test_pi postgres:15
    export TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:5433/test_pi
"""

import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def _require_test_db() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set — skipping integration tests")
    return TEST_DATABASE_URL


@pytest_asyncio.fixture(scope="function")
async def db_engine(_require_test_db):
    """Create a fresh schema for each test by re-creating all tables."""
    from src.db import Base

    # Importing models registers their metadata with Base.
    from src.models import account as _account  # noqa: F401
    from src.models import address as _address  # noqa: F401

    engine = create_async_engine(_require_test_db, future=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    SessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine) -> AsyncGenerator[AsyncClient, None]:
    """HTTP test client wired to the in-memory FastAPI app and the test DB."""
    from src.db import get_db
    from src.main import create_app

    SessionLocal = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async def _override_get_db():
        async with SessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": os.environ.get("API_KEY", "test-api-key-ci")},
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def created_account_id(client) -> uuid.UUID:
    """Create an account and return its UUID — many tests need an account FK."""
    payload = {
        "customer_number": f"CUST-{uuid.uuid4().hex[:8].upper()}",
        "first_name": "Jane",
        "last_name": "Smith",
        "email": f"{uuid.uuid4().hex[:8]}@example.com",
    }
    resp = await client.post("/accounts", json=payload)
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])
