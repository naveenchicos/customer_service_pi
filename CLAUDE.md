# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Customer Service Pi

Account management service: create/update accounts, look up by customer number or search criteria. Async FastAPI on GKE Autopilot with PostgreSQL via Cloud SQL.

## Stack

- **Runtime:** Python 3.14 (pinned in `.python-version`), FastAPI (async), Uvicorn
- **Platform:** GKE Autopilot (Google Cloud), region: `us-central1`
- **Database:** PostgreSQL 15 via Cloud SQL (Auth Proxy on `localhost:5432`)
- **Gateway:** Apigee (external) → GKE Gateway API (internal) → this service
- **Downstream:** Customer Service (GKE, 500ms timeout), Algolia (2s timeout)
- **Registry:** Google Artifact Registry (`us-central1-docker.pkg.dev/pi-dev-ai-493823/pydevrepo`)
- **IaC:** Terraform in `infra/`

## Commands

```bash
# Activate virtual environment (required before all commands)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt        # production deps
pip install -r requirements-dev.txt    # + test/lint tools

# Run the app locally
uvicorn src.main:app --reload

# Tests
pytest tests/unit/                          # unit (no DB/network needed)
pytest tests/integration/                   # integration (requires docker-compose)
pytest tests/unit/test_schemas.py::TestAccountCreate::test_valid_payload_accepted  # single test

# Lint / type-check / format
flake8 src/ tests/
mypy src/
black src/ tests/

# Database migrations
alembic upgrade head                        # apply all pending migrations
alembic revision --autogenerate -m "desc"  # generate migration from model changes
alembic downgrade -1                        # roll back one migration
alembic current                             # show applied revision

# Regenerate OpenAPI spec (run after changing routers/schemas)
python -c "
import os, json
os.environ.update({'DATABASE_URL':'postgresql+asyncpg://u:p@localhost/db',
  'CUSTOMER_SERVICE_URL':'http://localhost','API_KEY':'x','ENVIRONMENT':'local'})
from src.main import create_app
with open('docs/openapi.json','w') as f: json.dump(create_app().openapi(), f, indent=2)
"

# Deploy (see Skills section)
docker build -t us-central1-docker.pkg.dev/pi-dev-ai-493823/pydevrepo/ol-service:TAG .
docker push us-central1-docker.pkg.dev/pi-dev-ai-493823/pydevrepo/ol-service:TAG
kubectl apply -f k8s/ -n production
kubectl rollout status deployment/ol-service -n production --timeout=3m
```

## Architecture

### `src/` layout

```text
src/
  main.py               # FastAPI app factory + lifespan (wires up all clients, middleware, routers)
  config.py             # Pydantic Settings — validated at startup; get_settings() is lru_cached
  db.py                 # Async SQLAlchemy engine, SessionLocal, Base, get_db() dependency
  middleware/
    correlation_id.py   # Injects X-Correlation-ID on every request (generates if absent)
    security_headers.py # OWASP A05 headers: CSP, HSTS, X-Frame-Options, etc.
    api_key_auth.py     # OWASP A07: constant-time X-API-Key validation; /health exempt
  models/
    account.py          # SQLAlchemy ORM — accounts table, UUID PK, soft-delete via status
  schemas/
    account.py          # Pydantic: AccountCreate, AccountUpdate, AccountResponse, PaginatedAccounts, ErrorDetail
  services/
    account_service.py  # All DB logic: create, get_by_id, get_by_customer_number, search, update, deactivate
  clients/
    customer_client.py  # httpx.AsyncClient + 500ms timeout + @circuit breaker (not yet: redis, algolia)
  routers/
    accounts.py         # POST/GET/PATCH/DELETE /accounts — full OpenAPI docs on every endpoint
    health.py           # GET /health, GET /health/dependencies
```

### Request flow

```text
Apigee → GKE Gateway (3s timeout) → CorrelationIdMiddleware → SecurityHeadersMiddleware
  → ApiKeyAuthMiddleware → Router → Service → DB (Cloud SQL via Auth Proxy)
                                             ↘ Customer Service client (500ms, circuit breaker)
```

### REST API surface

| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/accounts` | Create account (201) |
| GET | `/accounts` | Search + paginate (`?query=&status=&page=&page_size=`) |
| GET | `/accounts/{id}` | Get by UUID |
| GET | `/accounts/by-customer/{number}` | Get by customer number |
| PATCH | `/accounts/{id}` | Partial update (only provided fields change) |
| DELETE | `/accounts/{id}` | Soft-delete (sets status=inactive) |
| GET | `/health` | Liveness probe |
| GET | `/health/dependencies` | Circuit breaker state for all downstream clients |

Full spec: `docs/openapi.json` — regenerate after changing routers/schemas (see Commands).
Interactive docs available at `/docs` in `local` and `staging` environments only.

### OWASP controls implemented

| Control | Where |
| ------- | ----- |
| A01 Broken Access Control | UUID PKs (no sequential IDs); soft-delete preserves audit trail |
| A03 Injection | Pydantic field validators (regex, length bounds, control-char check); SQLAlchemy ORM parameterised queries |
| A05 Security Misconfiguration | `SecurityHeadersMiddleware` (CSP, HSTS, X-Frame-Options, nosniff); `/docs` disabled in production; non-root container user; `readOnlyRootFilesystem: true` in K8s |
| A07 Auth Failures | `ApiKeyAuthMiddleware` with `hmac.compare_digest` (timing-safe); `SecretStr` in config |
| A09 Logging Failures | Structured JSON logs; `X-Correlation-ID` on every request; stack traces logged server-side only, never in responses |

### Client / resilience pattern

All downstream clients follow the same pattern (see `.claude/skills/resilience_SKILL.md` for full code):

1. Module-level `_client` variable, initialised to `None`
2. `init_*()` / `close_*()` called in `main.py` lifespan — **never at module level**
3. Every outbound call wrapped in `asyncio.wait_for(…, timeout=N)` **and** `@circuit(…)` decorator
4. Graceful fallback helpers (e.g. `get_customer_or_none`) return `None` instead of raising

### Timeout budget

| Client           | Timeout | Breaker threshold   |
| ---------------- | ------- | ------------------- |
| Customer Service | 500ms   | 5 failures / 30s    |
| Redis            | 200ms   | (not yet built)     |
| Algolia          | 2s      | (not yet built)     |
| Cloud SQL        | 5s      | n/a (pool settings) |

Gateway enforces `request: 3s` / `backendRequest: 2s`. Sequential downstream calls must fit within 2s; use `asyncio.gather` for parallel calls.

### Health endpoints

- `GET /health` — liveness (GKE kubelet probe; no auth required)
- `GET /health/dependencies` — circuit breaker state for each downstream. **Check this before restarting pods.**

### Python 3.14 compatibility note

SQLAlchemy `Mapped[X | None]` annotations break on Python 3.14 due to a `Union.__getitem__` change. Use `Mapped[Optional[X]]` from `typing` in all ORM models instead.

## Environment Variables

| Variable | Description |
| -------- | ----------- |
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@localhost:5432/ol_db` |
| `CUSTOMER_SERVICE_URL` | Internal K8s DNS: `http://customer-service.production.svc.cluster.local` |
| `API_KEY` | Shared secret validated on every non-health request via `X-API-Key` header |
| `CIRCUIT_BREAKER_THRESHOLD` | Consecutive failures before circuit opens (default: 5) |
| `CIRCUIT_BREAKER_RECOVERY` | Seconds before half-open attempt (default: 30) |
| `ENVIRONMENT` | `production` / `staging` / `local` |
| `LOG_LEVEL` | `INFO` in production; `DEBUG` locally |
| `REDIS_URL` | Redis connection string (client not yet implemented) |
| `ALGOLIA_APP_ID` / `ALGOLIA_API_KEY` / `ALGOLIA_INDEX` | Algolia credentials (client not yet implemented) |

## Gotchas

- **Cloud SQL:** Always connect via Auth Proxy sidecar — never direct IP. Sidecar listens on `localhost:5432`. If pods fail to start, check: `kubectl logs POD_NAME -c cloud-sql-proxy -n production`
- **Cloud SQL Auth Proxy — GKE auth:** The proxy uses Application Default Credentials. Without Workload Identity, ADC resolves to the node compute SA which has no Cloud SQL access. Fix: mount SA key as K8s Secret (`cloud-sql-sa-key`) and set `GOOGLE_APPLICATION_CREDENTIALS=/secrets/cloudsql/key.json` on the proxy container. Diagnosis: `kubectl logs POD -c cloud-sql-proxy -n production` — a 403 `NOT_AUTHORIZED` means wrong credentials, not a network issue.
- **Alembic migrations on GKE:** Run inside a pod, not locally against Cloud SQL: `kubectl exec -n production $(kubectl get pod -n production -l app=ol-service -o jsonpath='{.items[0].metadata.name}') -c ol-service -- alembic upgrade head`
- **Artifact Registry repo name:** The actual repo is `pydevrepo` (not `ol-repo`). Full image path: `us-central1-docker.pkg.dev/pi-dev-ai-493823/pydevrepo/ol-service:TAG`
- **Docker credential helper:** If `docker build` fails with `docker-credential-gcr: executable file not found`, check `~/.docker/config.json`. The entry `"https://index.docker.io/v1/": "gcr"` is wrong — change to `"desktop"`.
- **CI/CD — flake8 line length:** Default is 79. Project uses 100 (`setup.cfg`). Always run `flake8 src/ tests/` and `black src/ tests/` locally before pushing.
- **CI/CD — mypy + Pydantic Settings:** `get_settings()` returns `Settings()` which mypy flags as missing required args. Suppress with `# type: ignore[call-arg]` — Pydantic reads from env vars, not constructor args.
- **CI/CD — mypy + circuitbreaker:** `circuitbreaker` has no type stubs. Suppressed in `setup.cfg` under `[mypy-circuitbreaker.*] ignore_missing_imports = True`.
- **CI/CD smoke test — old pods:** After a rollout, `kubectl exec` can hit a Terminating/Failed pod from the old ReplicaSet. Always select the newest pod: `--sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}'`
- **Workload Identity Federation:** Creating a WIF pool requires `roles/iam.workloadIdentityPoolAdmin` (project owner level). The `pi-ai-dev-engineer` SA cannot do this. Current workaround: `GCP_SA_KEY` GitHub secret with SA JSON key. Migrate to WIF when project owner access is available.
- **GKE Gateway requires a real domain:** `k8s/gateway.yaml` provisions a GCP load balancer with managed TLS. It will not work with fake domains (no DNS verification). Apply only after a real domain is pointed at the load balancer IP.
- **Apigee Spike Arrest:** 500 req/s. If traffic exceeds this, tune Apigee first — do not scale GKE pods.
- **Namespace:** Production = `production`, staging = `staging`. Always verify before applying: `kubectl config current-context`
- **Circuit breakers:** If a breaker is open, check `GET /health/dependencies` before restarting pods.
- **HPA lag:** Min 3 replicas, scales on CPU. Pre-scale for peak events: `kubectl scale deployment/ol-service --replicas=15 -n production`
- **Correlation IDs:** `X-Correlation-ID` injected by middleware on every request. Always use it when searching Cloud Logging.
- **Gateway timeout:** 3s total; `backendRequest` must always be less than `request` or the backend timeout never fires.
- **HTTPRoute "Accepted" ≠ traffic flowing:** Always check `ResolvedRefs` condition too — a wrong backend service name gives `Accepted=True` but silently drops all traffic.
- **Retry policy:** Never retry on `500` (application errors); only on `503`, `504`, `connection-error`, `reset`. Max 2 attempts.
- **Holiday incident pattern:** No timeouts on outbound calls → one slow Redis response blocks all threads → OL receives requests but nothing reaches downstream. Always add `asyncio.wait_for` + `@circuit` on every client call.

## Skills (load when relevant)

- **Deploying to GKE:** read `.claude/skills/gke-deploy_SKILL.md`
- **Writing/updating K8s Gateway YAML:** read `.claude/skills/gateway-yaml_SKILL.md`
- **Adding resilience (circuit breaker, timeouts, retry):** read `.claude/skills/resilience_SKILL.md`
- **Write CI/CD pipelines:** use GitHub Actions

## Rules

- Never change scripts without approval. Root cause first, then wait for go-ahead.
- Data format issues get diagnosed and reported, not silently fixed.
- Scheduled run errors get logged and surfaced in the summary, not hidden.
- For unexpected issues, use this format: **issue → root cause → options → recommendation → awaiting decision**.
- Show diffs before replacing code; provide concise summaries.
- At end of a task, report: files modified, current kubectl context/namespace, open TODOs, TAG version if mid-deploy.
- Never run `kubectl delete` on production resources — use rollback or patch instead.
- Never deploy to production namespace without explicit user confirmation.
