"""
FastAPI application entry point.

Startup order (lifespan):
  1. Initialise Customer Service HTTP client (connection pool)
  2. Register middleware (order matters — outermost added last in Starlette)
  3. Register routers

Middleware execution order (request → response):
  CorrelationIdMiddleware   ← outermost; sets request.state.correlation_id first
  SecurityHeadersMiddleware ← adds OWASP headers to every response
  ApiKeyAuthMiddleware      ← validates API key; reads correlation_id set above

OpenAPI docs are disabled in production (OWASP A05).
"""

import logging
import logging.config
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.clients.customer_client import close_customer_client, init_customer_client
from src.config import Environment, get_settings
from src.middleware.api_key_auth import ApiKeyAuthMiddleware
from src.middleware.correlation_id import CorrelationIdMiddleware
from src.middleware.security_headers import SecurityHeadersMiddleware
from src.routers import accounts, health

settings = get_settings()

# ── Structured logging ────────────────────────────────────────────────────────
logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "pythonjsonlogger.json.JsonFormatter",
                "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json",
            }
        },
        "root": {"level": settings.log_level, "handlers": ["console"]},
    }
)

logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    logger.info(
        "Starting up",
        extra={
            "environment": settings.environment,
            "version": settings.service_version,
        },
    )
    await init_customer_client()
    yield
    logger.info("Shutting down")
    await close_customer_client()


# ── App factory ───────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    is_production = settings.environment == Environment.PRODUCTION

    app = FastAPI(
        title="Customer Service Pi",
        description=(
            "Account management API — create, update, look up, and manage customer accounts.\n\n"
            "## Authentication\n"
            "All endpoints (except `/health`) require an `X-API-Key` header.\n\n"
            "## Pagination\n"
            "`GET /accounts` returns paginated results. "
            "Use `page` and `page_size` query params.\n\n"
            "## Correlation IDs\n"
            "Every request and response carries an `X-Correlation-ID` header. "
            "Include this value when raising support tickets or searching logs."
        ),
        version=settings.service_version,
        # Disable interactive docs in production (OWASP A05)
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
        lifespan=lifespan,
        contact={
            "name": "Platform Engineering",
            "email": "platform@example.com",
        },
        license_info={"name": "Proprietary"},
    )

    # ── Middleware (added in reverse execution order — last added = outermost) ──
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(ApiKeyAuthMiddleware)

    # CORS — locked down for an internal API
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],  # no browser-based cross-origin access
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=[
            "X-API-Key",
            "X-Correlation-ID",
            "X-Caller-Identity",
            "Content-Type",
        ],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(accounts.router)
    app.include_router(health.router)

    # ── Exception handlers ────────────────────────────────────────────────────

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """
        Override FastAPI's default 422 response to use the ErrorDetail envelope
        and include the correlation ID.  Never expose raw Pydantic internals (OWASP A05).
        """
        correlation_id = getattr(request.state, "correlation_id", None)
        first_error = exc.errors()[0] if exc.errors() else {}
        field = " → ".join(str(loc) for loc in first_error.get("loc", []))
        message = f"Validation error on field '{field}': {first_error.get('msg', 'invalid value')}"

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": "VALIDATION_ERROR",
                "message": message,
                "correlation_id": correlation_id,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """
        Catch-all for unhandled exceptions.
        Logs the full traceback internally but returns a safe generic message (OWASP A05).
        """
        correlation_id = getattr(request.state, "correlation_id", None)
        logger.exception(
            "Unhandled exception",
            extra={"correlation_id": correlation_id, "path": request.url.path},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred. Please retry or contact support.",
                "correlation_id": correlation_id,
            },
        )

    return app


app = create_app()
