"""
Unit tests for CallerIdentityMiddleware.

Production:  requires X-Caller-Identity header (set by Apigee after token validation).
Local/staging: requires X-API-Key header (Apigee not in front).
/health is exempt from auth in both modes.
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.middleware.caller_identity_auth import CallerIdentityMiddleware
from src.middleware.correlation_id import CorrelationIdMiddleware


def _make_app(environment: str) -> FastAPI:
    os.environ["ENVIRONMENT"] = environment
    if environment == "production":
        os.environ.pop("API_KEY", None)
    else:
        os.environ["API_KEY"] = "test-api-key-ci"

    from src.config import get_settings

    get_settings.cache_clear()

    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(CallerIdentityMiddleware)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/accounts")
    def accounts():
        return {"items": []}

    return app


# ── Production mode ────────────────────────────────────────────────────────────


class TestProductionMode:
    @pytest.fixture(autouse=True)
    def client(self):
        self.client = TestClient(_make_app("production"), raise_server_exceptions=False)

    def test_missing_caller_identity_returns_401(self):
        resp = self.client.get("/accounts")
        assert resp.status_code == 401
        assert resp.json()["code"] == "UNAUTHORIZED"
        assert "WWW-Authenticate" in resp.headers
        assert resp.headers["WWW-Authenticate"].startswith("Bearer")

    def test_present_caller_identity_passes(self):
        resp = self.client.get("/accounts", headers={"X-Caller-Identity": "my-app"})
        assert resp.status_code == 200

    def test_health_exempt_no_header_needed(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200

    def test_api_key_not_accepted_in_production(self):
        # X-API-Key alone is not sufficient in production — X-Caller-Identity is required.
        resp = self.client.get("/accounts", headers={"X-API-Key": "test-api-key-ci"})
        assert resp.status_code == 401


# ── Local / staging mode ───────────────────────────────────────────────────────


class TestLocalMode:
    @pytest.fixture(autouse=True)
    def client(self):
        self.client = TestClient(_make_app("local"), raise_server_exceptions=False)

    def test_missing_api_key_returns_401(self):
        resp = self.client.get("/accounts")
        assert resp.status_code == 401
        assert resp.json()["code"] == "UNAUTHORIZED"
        assert resp.headers["WWW-Authenticate"].startswith("ApiKey")

    def test_wrong_api_key_returns_401(self):
        resp = self.client.get("/accounts", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

    def test_correct_api_key_passes(self):
        resp = self.client.get("/accounts", headers={"X-API-Key": "test-api-key-ci"})
        assert resp.status_code == 200

    def test_health_exempt_no_key_needed(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
