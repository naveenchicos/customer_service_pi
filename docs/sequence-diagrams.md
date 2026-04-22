# Sequence Diagrams — Customer Service Pi

---

## 1. Full Request Flow (Happy Path)

Shows a `POST /accounts` request travelling through every layer from the external client to the database.

```mermaid
sequenceDiagram
    autonumber

    actor Client as External Client
    participant Apigee as Apigee Gateway<br/>(external)
    participant GKEGateway as GKE Gateway API<br/>(timeout 3s / retry)
    participant CorrID as CorrelationId<br/>Middleware
    participant SecHdr as SecurityHeaders<br/>Middleware
    participant Auth as ApiKeyAuth<br/>Middleware
    participant Router as Accounts Router
    participant Service as Account Service
    participant DB as Cloud SQL<br/>(via Auth Proxy)
    participant CS as Customer Service<br/>(GKE internal)

    Client->>Apigee: POST /accounts<br/>X-API-Key: ***
    Note over Apigee: Validates external auth<br/>Spike arrest (500 req/s)<br/>Rate limiting / quota

    Apigee->>GKEGateway: Forward request
    Note over GKEGateway: Enforces request: 3s<br/>backendRequest: 2s<br/>Retry: 503/504/reset only

    GKEGateway->>CorrID: Forward to pod
    Note over CorrID: Reads X-Correlation-ID header<br/>Generates UUID if absent<br/>Validates UUID format (prevents log injection)<br/>Sets request.state.correlation_id

    CorrID->>SecHdr: next()
    Note over SecHdr: Adds to response:<br/>CSP, HSTS, X-Frame-Options<br/>Cache-Control: no-store<br/>Server: customer-service-pi

    SecHdr->>Auth: next()
    alt X-API-Key missing or invalid
        Auth-->>Client: 401 Unauthorized<br/>ErrorDetail{code: UNAUTHORIZED}<br/>WWW-Authenticate header
    else X-API-Key valid (hmac.compare_digest)
        Auth->>Router: next()
    end

    Router->>Router: Pydantic validates request body<br/>(regex, length, email format, control chars)
    alt Validation fails
        Router-->>Client: 422 Unprocessable Entity<br/>ErrorDetail{code: VALIDATION_ERROR}
    end

    Router->>Service: create_account(db, payload, caller_identity)
    Service->>Service: Normalise email to lowercase<br/>Uppercase customer_number

    Service->>DB: INSERT INTO accounts ...<br/>(SQLAlchemy ORM — parameterised)
    alt Duplicate customer_number or email
        DB-->>Service: IntegrityError
        Service-->>Router: ValueError("ACCOUNT_ALREADY_EXISTS")
        Router-->>Client: 409 Conflict<br/>ErrorDetail{code: ACCOUNT_ALREADY_EXISTS}
    else Insert successful
        DB-->>Service: Account row
        Service-->>Router: Account ORM object
    end

    Router-->>GKEGateway: 201 Created<br/>AccountResponse{id, customer_number, ...}<br/>X-Correlation-ID: <uuid>
    GKEGateway-->>Apigee: 201 Created
    Apigee-->>Client: 201 Created
```

---

## 2. GET /accounts/{id} with Customer Enrichment

Shows how the service attempts to enrich the account response with data from the downstream Customer Service, and degrades gracefully when it is unavailable.

```mermaid
sequenceDiagram
    autonumber

    actor Client as External Client
    participant Auth as ApiKeyAuth<br/>Middleware
    participant Router as Accounts Router
    participant Service as Account Service
    participant DB as Cloud SQL
    participant CB as Circuit Breaker<br/>(in-process)
    participant CS as Customer Service<br/>(GKE internal)

    Client->>Auth: GET /accounts/{id}<br/>X-API-Key: ***
    Auth->>Router: Authenticated ✓

    Router->>Service: get_account_with_customer_details(db, id)

    Service->>DB: SELECT * FROM accounts WHERE id = ?
    alt Account not found
        DB-->>Service: None
        Service-->>Router: ValueError("ACCOUNT_NOT_FOUND")
        Router-->>Client: 404 Not Found<br/>ErrorDetail{code: ACCOUNT_NOT_FOUND}
    else Account found
        DB-->>Service: Account row
    end

    Service->>CB: get_customer_or_none(customer_number)

    alt Circuit CLOSED (normal)
        CB->>CS: GET /customers/{customer_number}<br/>timeout: 500ms
        alt Customer Service responds in time
            CS-->>CB: 200 OK {customer details}
            CB-->>Service: {customer details}
        else Timeout > 500ms
            CS-->>CB: asyncio.TimeoutError
            Note over CB: failure_count++
            CB-->>Service: None (graceful fallback)
        else 5xx error
            CS-->>CB: HTTPStatusError 5xx
            Note over CB: failure_count++<br/>Opens if threshold reached
            CB-->>Service: None (graceful fallback)
        end
    else Circuit OPEN (downstream struggling)
        Note over CB: Short-circuits immediately<br/>No call to Customer Service
        CB-->>Service: None (graceful fallback)
    end

    Service-->>Router: (Account, customer_details | None)
    Router-->>Client: 200 OK<br/>AccountResponse{...}<br/>X-Correlation-ID: <uuid>
```

---

## 3. Circuit Breaker State Machine

```mermaid
stateDiagram-v2
    [*] --> Closed

    Closed --> Closed: Call succeeds
    Closed --> Open: 5 consecutive failures

    Open --> HalfOpen: After 30s recovery timeout
    Open --> Open: Call attempted → fail immediately\n(CircuitBreakerError — no network call made)

    HalfOpen --> Closed: Test call succeeds\n(failure_count reset to 0)
    HalfOpen --> Open: Test call fails\n(recovery timer resets)

    note right of Closed
        Normal operation.
        All calls pass through.
    end note

    note right of Open
        Downstream is struggling.
        GET /health/dependencies shows circuit_breaker: open.
        Check BEFORE restarting pods.
    end note

    note right of HalfOpen
        One test call allowed.
        Success → resume normal traffic.
        Fail → back to Open.
    end note
```

---

## 4. Middleware Execution Order

Middleware is added to FastAPI in reverse execution order (last added = outermost).

```mermaid
sequenceDiagram
    participant Network
    participant CorrID as CorrelationId<br/>(outermost)
    participant SecHdr as SecurityHeaders
    participant Auth as ApiKeyAuth<br/>(innermost)
    participant App as Router / Handler

    Note over Network,App: ── Inbound (request) ──────────────────────────────

    Network->>CorrID: HTTP request
    CorrID->>CorrID: Inject / validate X-Correlation-ID<br/>Set request.state.correlation_id
    CorrID->>SecHdr: call_next(request)
    SecHdr->>Auth: call_next(request)
    Auth->>Auth: hmac.compare_digest(api_key, expected)
    Auth->>App: call_next(request)
    App->>App: Pydantic validation → Service → DB

    Note over Network,App: ── Outbound (response) ────────────────────────────

    App-->>Auth: Response
    Auth-->>SecHdr: Response (or 401)
    SecHdr->>SecHdr: Append security headers to response
    SecHdr-->>CorrID: Response
    CorrID->>CorrID: Append X-Correlation-ID to response headers
    CorrID-->>Network: Final response
```

---

## 5. Database Migration Flow

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant Alembic
    participant Env as alembic/env.py
    participant Settings as src/config.py
    participant Proxy as Cloud SQL Auth Proxy<br/>(localhost:5432)
    participant DB as Cloud SQL PostgreSQL

    Dev->>Alembic: alembic upgrade head

    Alembic->>Env: load env.py
    Env->>Settings: get_settings()
    Settings-->>Env: DATABASE_URL from .env

    Env->>Env: asyncio.run(run_async_migrations())
    Env->>Proxy: Connect postgresql+asyncpg://...@localhost:5432/postgres
    Proxy->>DB: Encrypted tunnel to Cloud SQL

    DB-->>Env: Connection established
    Env->>DB: SELECT version_num FROM alembic_version
    DB-->>Env: Current revision (or empty)

    loop For each pending migration
        Env->>DB: BEGIN
        Env->>DB: CREATE TABLE / ALTER TABLE / CREATE INDEX ...
        Env->>DB: UPDATE alembic_version SET version_num = '000X'
        Env->>DB: COMMIT
        DB-->>Env: OK
    end

    Env-->>Dev: Done. Current head: 0001
```

---

## 6. CI/CD Pipeline Flow

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant GH as GitHub
    participant CI as CI Workflow<br/>(ci.yaml)
    participant CD as CD Workflow<br/>(cd.yaml)
    participant AR as Artifact Registry<br/>(GCP)
    participant GKE as GKE Cluster<br/>(production)
    participant Approval as GitHub Environment<br/>(production — manual gate)

    Dev->>GH: git push origin main

    GH->>CI: Trigger CI workflow
    CI->>CI: pip install -r requirements-dev.txt
    CI->>CI: flake8 src/ tests/
    CI->>CI: black --check src/ tests/
    CI->>CI: mypy src/
    CI->>CI: pytest tests/unit/

    alt CI fails
        CI-->>Dev: ❌ PR blocked — fix lint/tests first
    else CI passes
        CI-->>GH: ✅ All checks green
    end

    GH->>CD: Trigger CD workflow (main branch)
    CD->>CD: Run unit tests again (safety net)

    CD->>AR: docker build + push<br/>tag: sha-<commit>
    AR-->>CD: Image pushed ✓

    CD->>Approval: Request manual approval
    Approval-->>Dev: 🔔 Approve deployment to production?
    Dev-->>Approval: ✅ Approved

    CD->>GKE: kubectl set image deployment/ol-service<br/>ol-service=...registry.../ol-service:sha-<commit>
    GKE->>GKE: Rolling update (replicas: 3 → 3)<br/>Liveness probe must pass before old pod removed

    alt Rollout succeeds within 3 minutes
        GKE-->>CD: Rollout complete ✓
        CD->>GKE: curl /health smoke test
        GKE-->>CD: {"status": "ok"} ✓
        CD-->>Dev: ✅ Deployed successfully
    else Rollout fails
        GKE-->>CD: Timeout / pods not ready
        CD->>GKE: kubectl rollout undo deployment/ol-service
        GKE-->>CD: Rolled back to previous version
        CD-->>Dev: ❌ Deployment failed — rolled back
    end
```
