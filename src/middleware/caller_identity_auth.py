"""
Caller identity middleware (OWASP A07 — Identification and Authentication Failures).

In production, Apigee validates the Bearer token and injects X-Caller-Identity
with the authenticated client_id before forwarding to GKE. This middleware
requires that header to be present — any request that bypasses Apigee will
not have it and receives a 401.

In local/staging (no Apigee in front), falls back to X-API-Key validation
using constant-time comparison to prevent timing-based enumeration attacks.

Exempt paths (no auth required):
  /health        — GKE liveness probe
  /docs          — Swagger UI (non-production only; blocked in main.py for production)
  /openapi.json  — OpenAPI schema (non-production only)
  /redoc         — ReDoc UI (non-production only)
"""

import hmac

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.config import Environment, get_settings

CALLER_IDENTITY_HEADER = "X-Caller-Identity"
API_KEY_HEADER = "X-API-Key"

_EXEMPT_PREFIXES = (
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
)


class CallerIdentityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if any(request.url.path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        settings = get_settings()
        correlation_id = getattr(request.state, "correlation_id", "")

        if settings.environment == Environment.PRODUCTION:
            # In production Apigee sets this after validating the Bearer token.
            # Absence means the request bypassed the gateway.
            caller = request.headers.get(CALLER_IDENTITY_HEADER, "")
            if not caller:
                return JSONResponse(
                    status_code=401,
                    content={
                        "code": "UNAUTHORIZED",
                        "message": "Missing caller identity — request must pass through Apigee",
                        "correlation_id": correlation_id,
                    },
                    headers={
                        "WWW-Authenticate": 'Bearer realm="customer-service-pi"',
                        "X-Correlation-ID": correlation_id,
                    },
                )
        else:
            # Local / staging: Apigee is not in front — use static API key.
            api_key = request.headers.get(API_KEY_HEADER, "")
            expected = settings.api_key.get_secret_value() if settings.api_key else ""
            if not hmac.compare_digest(api_key.encode(), expected.encode()):
                return JSONResponse(
                    status_code=401,
                    content={
                        "code": "UNAUTHORIZED",
                        "message": "Missing or invalid API key",
                        "correlation_id": correlation_id,
                    },
                    headers={
                        "WWW-Authenticate": 'ApiKey realm="customer-service-pi"',
                        "X-Correlation-ID": correlation_id,
                    },
                )

        return await call_next(request)
