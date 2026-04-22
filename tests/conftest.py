"""
Shared pytest fixtures.

Settings override: call get_settings.cache_clear() before each test that
needs a different configuration so the lru_cache doesn't return stale values.
"""

import os

import pytest

# ── Set required env vars before any app module is imported ──────────────────
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/test_db")
os.environ.setdefault("CUSTOMER_SERVICE_URL", "http://customer-service.test.svc.cluster.local")
os.environ.setdefault("API_KEY", "test-api-key-ci")
os.environ.setdefault("ENVIRONMENT", "local")


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear the settings lru_cache before every test to allow env var overrides."""
    from src.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
