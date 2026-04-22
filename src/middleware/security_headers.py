"""
Security headers middleware (OWASP A05 — Security Misconfiguration).

Adds defensive HTTP headers to every response.  These headers are a
defence-in-depth layer — Apigee and the GKE Gateway may also set some of
them, but having them here ensures they are always present even during
local development or direct-to-pod port-forwarding.

Header rationale:
  X-Content-Type-Options    — prevents MIME-type sniffing (OWASP A05)
  X-Frame-Options           — prevents clickjacking
  Strict-Transport-Security — enforces HTTPS (meaningful in prod/staging)
  Content-Security-Policy   — restricts resource origins for API-only service
  Cache-Control             — prevents caching of API responses with PII
  Referrer-Policy           — limits referrer leakage
  Permissions-Policy        — disables unneeded browser features
  X-XSS-Protection          — legacy browsers XSS filter (belt-and-suspenders)
  Server                    — hides server implementation details (OWASP A05)
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )
        # API-only service: no scripts, no external resources needed
        response.headers["Content-Security-Policy"] = "default-src 'none'"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Mask server identity — do not reveal runtime or framework version
        response.headers["Server"] = "customer-service-pi"

        return response
