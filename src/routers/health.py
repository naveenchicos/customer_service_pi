"""
Health check endpoints.

GET /health              — liveness probe (GKE kubelet uses this)
GET /health/dependencies — readiness-style probe that reports the live state
                           of all downstream dependencies and circuit breakers.

Check /health/dependencies BEFORE restarting pods when something looks wrong.
An open circuit breaker here means the downstream is struggling, not this service.
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from src.clients import customer_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["Health"])


class HealthResponse(BaseModel):
    status: str


class DependencyStatus(BaseModel):
    status: str
    circuit_breaker: str


class DependenciesResponse(BaseModel):
    status: str
    dependencies: dict[str, DependencyStatus]


def _breaker_state(fn: object) -> str:
    """Extract circuit breaker state string from a @circuit-decorated function."""
    cb = getattr(fn, "__self__", None)
    if cb is None:
        return "unknown"
    return "open" if cb.opened else "closed"


@router.get(
    "",
    response_model=HealthResponse,
    summary="Liveness probe",
    response_description="Service is alive",
    tags=["Health"],
)
async def health() -> HealthResponse:
    """
    Simple liveness check — returns 200 if the process is running.
    GKE kubelet calls this; it must not require authentication (exempt in api_key_auth.py).
    """
    return HealthResponse(status="ok")


@router.get(
    "/dependencies",
    response_model=DependenciesResponse,
    summary="Dependency health and circuit breaker state",
    response_description="Live status of all downstream dependencies",
    tags=["Health"],
)
async def health_dependencies() -> DependenciesResponse:
    """
    Reports the connectivity and circuit breaker state for each downstream service.

    - **status: ok** — dependency is reachable
    - **status: error** — dependency returned an error or is unreachable
    - **circuit_breaker: open** — breaker has tripped; calls are being short-circuited
    - **circuit_breaker: closed** — breaker is healthy; calls are flowing normally

    **Important:** an open circuit breaker indicates the *downstream* is struggling.
    Check this endpoint before restarting this service's pods.
    """
    deps: dict[str, DependencyStatus] = {}
    overall = "ok"

    # ── Customer Service ──────────────────────────────────────────────────
    cb_state = _breaker_state(customer_client.get_customer)
    if cb_state == "open":
        deps["customer_service"] = DependencyStatus(
            status="degraded", circuit_breaker=cb_state
        )
        overall = "degraded"
    else:
        deps["customer_service"] = DependencyStatus(
            status="ok", circuit_breaker=cb_state
        )

    return DependenciesResponse(status=overall, dependencies=deps)
