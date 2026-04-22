"""
Customer Service HTTP client.

Wraps the downstream Customer Service (internal GKE DNS) with:
  - Shared async httpx client (connection pool created once in lifespan)
  - Per-call 500ms hard timeout (asyncio.wait_for)
  - Circuit breaker: opens after 5 consecutive failures, recovers after 30s
  - 5xx errors trip the breaker; 4xx errors are client errors and do NOT trip it

See .claude/skills/resilience_SKILL.md for the full pattern rationale.
"""

import asyncio
import logging

import httpx
from circuitbreaker import CircuitBreakerError, circuit

from src.config import get_settings

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


async def init_customer_client() -> None:
    """Called once from FastAPI lifespan on startup."""
    global _client
    settings = get_settings()
    _client = httpx.AsyncClient(
        base_url=str(settings.customer_service_url),
        timeout=httpx.Timeout(
            connect=0.5,
            read=settings.customer_service_timeout,
            write=0.5,
            pool=0.1,
        ),
        limits=httpx.Limits(
            max_connections=settings.customer_service_pool_size,
            max_keepalive_connections=20,
        ),
        headers={"User-Agent": "customer-service-pi/1.0"},
    )


async def close_customer_client() -> None:
    """Called once from FastAPI lifespan on shutdown."""
    global _client
    if _client:
        await _client.aclose()
        _client = None


def _get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError(
            "Customer Service client is not initialised. "
            "Ensure init_customer_client() is called in the FastAPI lifespan."
        )
    return _client


@circuit(failure_threshold=5, recovery_timeout=30, expected_exception=Exception)
async def get_customer(customer_number: str) -> dict:
    """
    Fetch a customer record by customer number from the Customer Service.

    Raises:
        TimeoutError: downstream did not respond within 500ms
        httpx.HTTPStatusError: non-2xx response (5xx trips the circuit breaker)
        CircuitBreakerError: breaker is open; downstream is struggling
    """
    client = _get_client()
    try:
        response = await asyncio.wait_for(
            client.get(f"/customers/{customer_number}"),
            timeout=get_settings().customer_service_timeout,
        )
        if response.status_code >= 500:
            response.raise_for_status()  # 5xx → trips circuit breaker
        if response.status_code == 404:
            return {}  # customer not found — not a breaker failure
        response.raise_for_status()
        return response.json()
    except asyncio.TimeoutError:
        logger.warning(
            "Customer Service timed out",
            extra={"customer_number": customer_number},
        )
        raise TimeoutError(
            f"Customer Service did not respond within "
            f"{get_settings().customer_service_timeout}s for customer {customer_number}"
        )


async def get_customer_or_none(customer_number: str) -> dict | None:
    """
    Returns None when the circuit is open or the customer is not found,
    instead of raising.  Lets callers degrade gracefully.
    """
    try:
        result = await get_customer(customer_number)
        return result if result else None
    except CircuitBreakerError:
        logger.warning(
            "Customer Service circuit breaker is open — skipping enrichment",
            extra={"customer_number": customer_number},
        )
        return None
    except TimeoutError:
        return None
