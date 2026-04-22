---
name: resilience
description: >
  Add or review resilience patterns in OL Service: circuit breakers, per-call
  timeouts, connection pooling, and async client setup for Redis, Customer Service,
  and Algolia. Use when adding a new downstream client, fixing timeout issues,
  reviewing for peak-traffic readiness, or investigating the holiday incident pattern
  where OL received requests but nothing reached downstream services.
allowed-tools: Read, Write, Bash(pytest:*), Glob
---

# Resilience Patterns — OL Service

## The problem this solves (holiday incident recap)

Requests reached OL but never forwarded to Redis / Customer Service / Algolia.
Root cause: no timeouts on outbound calls. One slow Redis response (8ms → 8s under
load) blocked a thread. All 100 threads blocked. New requests queued then dropped.
Downstream services looked healthy because OL never actually called them.

**Fix layer by layer:**
1. Gateway API: timeout + retry (protects threads from upstream flooding OL)
2. OL code: per-call timeouts + circuit breaker (protects threads from slow downstream)
3. Redis client: connection pool with hard max (prevents pool exhaustion)

---

## Timeout budget per downstream

| Client | Timeout | Circuit breaker threshold | Rationale |
|---|---|---|---|
| Redis | 200ms | 50% failures / 10 req window | Cache — if slow, bypass and hit DB |
| Customer Service | 500ms | 50% failures / 10 req window | Internal GKE service, should be fast |
| Algolia | 2000ms | 40% failures / 5 req window | External, naturally slower |
| Cloud SQL (via SQLAlchemy) | 5000ms | n/a — use DB pool settings | DB queries can be slower |

Total downstream budget must stay under `backendRequest: 2s` set in Gateway HTTPRoute.
For sequential calls (Redis → Algolia), you have ~2s total. Use `asyncio.gather` for
parallel calls when possible.

---

## Redis client (`src/clients/redis_client.py`)

```python
import asyncio
from contextlib import asynccontextmanager
import redis.asyncio as aioredis
from circuitbreaker import circuit
from src.config import settings

# ── Connection pool (created once at startup via lifespan) ──
_pool: aioredis.ConnectionPool | None = None

def get_pool() -> aioredis.ConnectionPool:
    if _pool is None:
        raise RuntimeError("Redis pool not initialised — call init_redis() first")
    return _pool

async def init_redis() -> None:
    global _pool
    _pool = aioredis.ConnectionPool.from_url(
        settings.REDIS_URL,
        max_connections=settings.REDIS_POOL_SIZE,   # default: 20, do NOT exceed without load test
        socket_connect_timeout=0.5,                 # connection timeout
        socket_timeout=0.2,                         # per-operation timeout (200ms)
        decode_responses=True,
    )

async def close_redis() -> None:
    if _pool:
        await _pool.disconnect()

# ── Circuit breaker wrapping cache reads ──
# Opens after 50% failure rate over last 10 calls
# Stays open for 30s, then half-opens to test recovery
@circuit(failure_threshold=5, recovery_timeout=30, expected_exception=Exception)
async def redis_get(key: str) -> str | None:
    async with aioredis.Redis(connection_pool=get_pool()) as client:
        try:
            return await asyncio.wait_for(client.get(key), timeout=0.2)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Redis GET timed out for key: {key}")

@circuit(failure_threshold=5, recovery_timeout=30, expected_exception=Exception)
async def redis_set(key: str, value: str, ttl: int = 300) -> None:
    async with aioredis.Redis(connection_pool=get_pool()) as client:
        try:
            await asyncio.wait_for(client.set(key, value, ex=ttl), timeout=0.2)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Redis SET timed out for key: {key}")

# ── Graceful fallback helper ──
async def redis_get_or_none(key: str) -> str | None:
    """Returns None on circuit open or timeout — caller decides to fall back to DB."""
    try:
        return await redis_get(key)
    except Exception:
        return None   # circuit open or timeout — degrade gracefully
```

---

## Customer Service client (`src/clients/customer_client.py`)

```python
import asyncio
import httpx
from circuitbreaker import circuit
from src.config import settings

# ── Shared async HTTP client (created once at startup) ──
_client: httpx.AsyncClient | None = None

async def init_customer_client() -> None:
    global _client
    _client = httpx.AsyncClient(
        base_url=settings.CUSTOMER_SERVICE_URL,
        timeout=httpx.Timeout(
            connect=0.5,    # connection timeout
            read=0.5,       # read timeout (500ms hard limit)
            write=0.5,
            pool=0.1,       # time to acquire a connection from pool
        ),
        limits=httpx.Limits(
            max_connections=50,
            max_keepalive_connections=20,
        ),
    )

async def close_customer_client() -> None:
    if _client:
        await _client.aclose()

@circuit(failure_threshold=5, recovery_timeout=30, expected_exception=Exception)
async def get_customer(customer_id: str) -> dict:
    if _client is None:
        raise RuntimeError("Customer client not initialised")
    try:
        response = await asyncio.wait_for(
            _client.get(f"/customers/{customer_id}"),
            timeout=0.5
        )
        response.raise_for_status()
        return response.json()
    except asyncio.TimeoutError:
        raise TimeoutError(f"Customer Service timed out for customer: {customer_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code >= 500:
            raise   # 5xx counts as circuit breaker failure
        raise       # 4xx re-raised but does NOT count as failure (client error)
```

---

## Algolia client (`src/clients/algolia_client.py`)

```python
import asyncio
from algoliasearch.search_client import SearchClient
from circuitbreaker import circuit
from src.config import settings

_client = None

def init_algolia() -> None:
    global _client
    _client = SearchClient.create(
        settings.ALGOLIA_APP_ID,
        settings.ALGOLIA_API_KEY,
    )

@circuit(failure_threshold=2, recovery_timeout=60, expected_exception=Exception)
async def search_products(query: str, filters: str = "") -> dict:
    if _client is None:
        raise RuntimeError("Algolia client not initialised")
    index = _client.init_index(settings.ALGOLIA_INDEX)
    try:
        # Algolia SDK is sync — run in executor with timeout
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: index.search(query, {"filters": filters})
            ),
            timeout=2.0    # 2s hard limit
        )
        return result
    except asyncio.TimeoutError:
        raise TimeoutError(f"Algolia search timed out for query: {query}")
```

---

## App lifespan wiring (`src/main.py`)

All clients must be initialised in the FastAPI lifespan — never at module level.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.clients.redis_client import init_redis, close_redis
from src.clients.customer_client import init_customer_client, close_customer_client
from src.clients.algolia_client import init_algolia

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_redis()
    await init_customer_client()
    init_algolia()
    yield
    # Shutdown
    await close_redis()
    await close_customer_client()

app = FastAPI(lifespan=lifespan)
```

---

## Health endpoint with dependency status (`src/routers/health.py`)

Always expose circuit breaker state — this was missing during the holiday incident
and made diagnosis very slow.

```python
from fastapi import APIRouter
from circuitbreaker import CircuitBreaker
import redis.asyncio as aioredis
from src.clients.redis_client import get_pool

router = APIRouter()

@router.get("/health")
async def health():
    return {"status": "ok"}

@router.get("/health/dependencies")
async def health_dependencies():
    """Reports live state of all downstream dependencies and circuit breakers."""
    results = {}

    # Redis connectivity check
    try:
        async with aioredis.Redis(connection_pool=get_pool()) as r:
            await r.ping()
        results["redis"] = "ok"
    except Exception as e:
        results["redis"] = f"error: {str(e)}"

    # Circuit breaker states (open = downstream is struggling)
    from src.clients import redis_client, customer_client, algolia_client
    results["circuit_breakers"] = {
        "redis": _breaker_state(redis_client.redis_get),
        "customer_service": _breaker_state(customer_client.get_customer),
        "algolia": _breaker_state(algolia_client.search_products),
    }
    return results

def _breaker_state(fn) -> str:
    cb = getattr(fn, "__self__", None)
    if cb is None:
        return "unknown"
    return "open" if cb.opened else "closed"
```

---

## Parallel downstream calls (when order allows)

If Redis cache miss requires both Customer Service and Algolia, call them in parallel
to stay within the 2s Gateway budget:

```python
import asyncio
from src.clients.redis_client import redis_get_or_none
from src.clients.customer_client import get_customer
from src.clients.algolia_client import search_products

async def get_order_page(order_id: str, customer_id: str, query: str) -> dict:
    # Check cache first
    cached = await redis_get_or_none(f"order:{order_id}")
    if cached:
        return {"source": "cache", "data": cached}

    # Cache miss — fetch customer + search in parallel
    customer_task = asyncio.create_task(get_customer(customer_id))
    search_task = asyncio.create_task(search_products(query))

    # gather with return_exceptions=True — one failure doesn't cancel the other
    customer, search = await asyncio.gather(
        customer_task, search_task, return_exceptions=True
    )

    return {
        "customer": customer if not isinstance(customer, Exception) else None,
        "search": search if not isinstance(search, Exception) else None,
    }
```

---

## Required dependencies (`requirements.txt` additions)

```
circuitbreaker>=2.0.0     # circuit breaker decorator
redis[asyncio]>=5.0.0     # async Redis with connection pool
httpx>=0.27.0             # async HTTP client for Customer Service
algoliasearch>=3.0.0      # Algolia SDK
```

---

## Testing circuit breakers

```bash
# Unit test — mock the downstream, force failures, verify breaker opens
pytest tests/unit/test_circuit_breaker.py -v

# Integration test — requires docker-compose
# Stops Redis container mid-test to verify graceful degradation
pytest tests/integration/test_resilience.py -v
```

Key test assertions:
- After N failures, circuit opens and subsequent calls fail immediately (no wait)
- After `recovery_timeout` seconds, circuit half-opens and allows one test call
- `GET /health/dependencies` correctly reports open circuits
- `redis_get_or_none` returns `None` (not exception) when circuit is open
