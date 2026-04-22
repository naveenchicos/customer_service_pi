"""
API key authentication middleware (OWASP A07 — Identification and Authentication Failures).

Every request must carry a valid API key in the X-API-Key header.
The key is compared using hmac.compare_digest to prevent timing-based
enumeration attacks.

Exempt paths:
  /health    — liveness probe used by GKE; must not require auth
  /docs      — Swagger UI (only accessible in non-production environments)
  /openapi.json  — OpenAPI schema (only accessible in non-production)
  /redoc     — ReDoc UI (only accessible in non-production)

In production, /docs and /openapi.json are blocked entirely in main.py
so the exemption here only matters for local/staging.
"""

import hmac

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.config import get_settings

API_KEY_HEADER = "X-API-Key"

_EXEMPT_PREFIXES = (
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
)


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if any(request.url.path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        api_key = request.headers.get(API_KEY_HEADER, "")
        expected = get_settings().api_key.get_secret_value()

        # Constant-time comparison — prevents timing oracle (OWASP A07)
        if not hmac.compare_digest(api_key.encode(), expected.encode()):
            return JSONResponse(
                status_code=401,
                content={
                    "code": "UNAUTHORIZED",
                    "message": "Missing or invalid API key",
                    "correlation_id": getattr(request.state, "correlation_id", None),
                },
                headers={
                    "WWW-Authenticate": 'ApiKey realm="customer-service-pi"',
                    "X-Correlation-ID": getattr(request.state, "correlation_id", ""),
                },
            )

        return await call_next(request)
