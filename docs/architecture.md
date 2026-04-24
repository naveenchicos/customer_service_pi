# Architecture & Request Flow

End-to-end design of the Customer Service Pi service — every GCP component a request touches,
in order, with the role each plays.

---

## System Overview

```
Internet
   │
   │  HTTPS (TLS 1.2/1.3)
   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  GCP — Global Edge                                                           │
│                                                                              │
│  Global External Load Balancer (34.107.235.25)                              │
│    └── Google Managed SSL Cert  (api.34-107-235-25.nip.io)                 │
│    └── PSC NEG  ──────────────────────────────────────────────► Apigee X   │
└─────────────────────────────────────────────────────────────────────────────┘
                                                                      │
                                            OAuth token request?      │
                                            ├── YES → issue token ────┘ (response)
                                            │
                                            └── NO  → verify token
                                                      inject X-Caller-Identity
                                                      strip Authorization header
                                                      forward to ILB ──────────┐
                                                                                │
┌───────────────────────────────────────────────────────────────────────────── ┼ ──┐
│  GKE Autopilot Cluster — us-central1   (VPC internal)                        │   │
│                                                                               │   │
│  Internal Load Balancer  (ol-service-ilb, 10.128.0.5:80)  ◄──────────────────┘   │
│    └── Pod (ol-service)                                                           │
│          ├── Cloud SQL Auth Proxy sidecar ──► Cloud SQL (PostgreSQL 18)          │
│          └── FastAPI app                                                          │
│                ├── CorrelationIdMiddleware                                        │
│                ├── SecurityHeadersMiddleware                                      │
│                ├── CallerIdentityMiddleware                                       │
│                └── Router → AccountService → DB                                  │
│                                           └──► Customer Service  (500ms)         │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## GCP Components

| Component | Type | Role | Key Config |
|-----------|------|------|------------|
| **Global External LB** | Cloud Load Balancing | Terminates TLS, receives public traffic | Static IP `34.107.235.25`; HTTPS only |
| **Google Managed SSL Cert** | Certificate Manager | Provisions and auto-renews TLS cert | Domain: `api.34-107-235-25.nip.io` (nip.io = no DNS registration needed) |
| **PSC NEG** | Private Service Connect NEG | Routes LB traffic into Apigee's VPC without public exposure | Points to Apigee X service attachment |
| **Apigee X** | API Gateway | OAuth 2.0 token issuance and verification; rate limiting; caller identity injection | Org: `pi-dev-ai-493823`; Env: `eval`; Base path: `/v1/customer-service` |
| **Apigee API Product** | Apigee | Defines what paths and operations are allowed | `customer-service-pi-product`; `apiResources: []` = all paths |
| **Apigee App** | Apigee | Issues `client_id` / `client_secret` to callers | `customer-service-pi-app` |
| **Internal Load Balancer** | Cloud Load Balancing | Routes Apigee traffic to GKE pods inside VPC | `ol-service-ilb`; IP `10.128.0.5`; port 80→8000 |
| **GKE Autopilot Cluster** | GKE | Runs the application pods; manages node provisioning | `ol-cluster`; region `us-central1`; REGULAR release channel |
| **GKE Deployment** | Kubernetes | Manages pod lifecycle; rolling updates | `ol-service`; min 3 replicas; HPA on CPU |
| **Cloud SQL** | Cloud SQL | PostgreSQL database | Instance: `py-dev-ai`; version: PostgreSQL 18; `db-perf-optimized-N-8 ENTERPRISE_PLUS` |
| **Cloud SQL Auth Proxy** | Sidecar container | Authenticates and encrypts DB connections | Listens on `localhost:5432`; SA key via K8s Secret |
| **Artifact Registry** | Container Registry | Stores Docker images | `us-central1-docker.pkg.dev/pi-dev-ai-493823/pydevrepo/ol-service` |
| **K8s Secret (`ol-service-secrets`)** | Kubernetes | Holds `DATABASE_URL`, env config | Mounted as env vars in the app container |
| **K8s Secret (`cloud-sql-sa-key`)** | Kubernetes | SA JSON key for Cloud SQL Auth Proxy | Mounted at `/secrets/cloudsql/key.json` |

---

## Flow 1 — Get an OAuth Token

Used once before making API calls. Token is valid for 1 hour.

```
Client
  │
  │  POST https://api.34-107-235-25.nip.io/v1/customer-service/oauth/token
  │  Authorization: Basic base64(client_id:client_secret)
  │  Body: grant_type=client_credentials
  │
  ▼
Global External LB
  • Terminates TLS
  • Forwards to Apigee via PSC NEG

  ▼
Apigee X — OAuthV2-GenerateToken policy
  • Validates client_id and client_secret against the App registry
  • Verifies the App is associated with an active API Product
  • Issues access token (JWT), expires_in: 3599
  • Does NOT forward this request to GKE — Apigee handles it entirely

  ▼
Response back to client:
  HTTP 200
  {
    "access_token": "...",
    "token_type": "Bearer",
    "expires_in": 3599
  }
```

---

## Flow 2 — API Request (e.g. GET /accounts)

```
Client
  │
  │  GET https://api.34-107-235-25.nip.io/v1/customer-service/accounts
  │  Authorization: Bearer <access_token>
  │
  ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 1 — DNS Resolution                                         │
│                                                                  │
│  nip.io wildcard DNS resolves api.34-107-235-25.nip.io          │
│  → 34.107.235.25 (no registration needed, works with GCP certs) │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 2 — Global External Load Balancer                          │
│                                                                  │
│  • Terminates TLS using Google Managed SSL Certificate           │
│  • Decrypts HTTPS → HTTP internally                             │
│  • Checks host header: api.34-107-235-25.nip.io                 │
│  • Routes to backend: Apigee via PSC NEG                        │
│  • Custom request header injected: Host: api.34-107-235-25.nip.io│
│    (required so Apigee can match the environment group)          │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 3 — Apigee X (Policy Execution)                            │
│                                                                  │
│  Policy 1 — OAuthV2-VerifyToken                                  │
│    • Reads Authorization header                                  │
│    • Verifies Bearer token signature and expiry                  │
│    • Checks token is associated with an active API Product       │
│    • Checks the product's apiResources allow this path           │
│    • Enforces Spike Arrest: max 500 req/s                        │
│    → 401 if token missing, invalid, or expired                   │
│                                                                  │
│  Policy 2 — AssignMessage-InjectCaller                           │
│    • Sets X-Caller-Identity: {client_id} on the request          │
│    • GKE uses this to know who called (audit trail)              │
│                                                                  │
│  Policy 3 — AssignMessage-StripAuth                              │
│    • Removes Authorization: Bearer header                        │
│    • GKE never sees the token — reduces attack surface           │
│                                                                  │
│  Target: http://10.128.0.5:80  (Internal LB, VPC-only)          │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               │  HTTP (plain, VPC-internal only)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 4 — Internal Load Balancer (ol-service-ilb)                │
│                                                                  │
│  • IP: 10.128.0.5, Port: 80                                      │
│  • VPC-internal only — not reachable from the internet           │
│  • Annotation: networking.gke.io/load-balancer-type: "Internal" │
│  • Distributes across healthy ol-service pods on port 8000       │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 5 — GKE Pod (ol-service container, port 8000)              │
│                                                                  │
│  Middleware chain (every request passes through all layers):     │
│                                                                  │
│  ┌─ CorrelationIdMiddleware ────────────────────────────────┐   │
│  │  Reads X-Correlation-ID header                           │   │
│  │  If absent: generates UUID and injects it                │   │
│  │  Echoes it back on every response                        │   │
│  │  Used for tracing across logs and downstream calls       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─ SecurityHeadersMiddleware ──────────────────────────────┐   │
│  │  Adds OWASP A05 headers on every response:               │   │
│  │    Content-Security-Policy                               │   │
│  │    Strict-Transport-Security (HSTS)                      │   │
│  │    X-Frame-Options: DENY                                 │   │
│  │    X-Content-Type-Options: nosniff                       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─ CallerIdentityMiddleware ───────────────────────────────┐   │
│  │  PRODUCTION:                                             │   │
│  │    Reads X-Caller-Identity header (set by Apigee)        │   │
│  │    → 401 if missing (request didn't come through Apigee) │   │
│  │  LOCAL / STAGING:                                        │   │
│  │    Falls back to X-API-Key header check                  │   │
│  │    Uses hmac.compare_digest (timing-safe)                │   │
│  │  /health endpoint is exempt — no auth check              │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  FastAPI Router → AccountService                                 │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 6 — Service Layer (AccountService)                         │
│                                                                  │
│  Path A — Database read/write                                    │
│    AccountService                                                │
│      └── SQLAlchemy ORM (async, parameterised queries)          │
│            └── asyncpg driver                                    │
│                  └── localhost:5432  (Auth Proxy listens here)   │
│                        └── Cloud SQL Auth Proxy sidecar          │
│                              └── Cloud SQL PostgreSQL 18         │
│                                    (TLS, IAM-authenticated)      │
│                                                                  │
│  Path B — Customer Service lookup (if needed)                    │
│    customer_client.py                                            │
│      ├── asyncio.wait_for(timeout=0.5s)                         │
│      ├── @circuit(threshold=5, recovery=30s)                    │
│      └── http://customer-service.production.svc.cluster.local   │
│            → returns None gracefully if circuit open or timeout  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 7 — Response path (reverse of request)                     │
│                                                                  │
│  FastAPI serialises response (Pydantic schema → JSON)            │
│  SecurityHeadersMiddleware adds OWASP headers                    │
│  CorrelationIdMiddleware echoes X-Correlation-ID                 │
│  GKE pod → Internal LB → Apigee → External LB → TLS encrypt     │
│  → Client receives HTTPS response                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Timeout Budget

Every component enforces a deadline. They must nest within each other:

```
Client request
  └── Global LB (no timeout — relies on backend)
        └── Apigee (60s default, effectively no limit here)
              └── GKE Gateway (3s request / 2s backendRequest)  ← hard ceiling
                    └── FastAPI handler
                          ├── Cloud SQL query ──────── 5s pool timeout
                          └── Customer Service ──────  0.5s asyncio.wait_for
```

**Rule:** sequential downstream calls must complete within 2s total (GKE backendRequest limit). Use `asyncio.gather` if calling multiple services in parallel.

---

## Security Controls — Layer by Layer

| Layer | What it protects | Mechanism |
|-------|-----------------|-----------|
| Global LB | Encrypts transit | TLS 1.2/1.3, Google Managed SSL Cert |
| Apigee | Blocks unauthenticated callers | OAuth 2.0 Bearer token (client credentials flow) |
| Apigee | Prevents token forwarding to backend | AssignMessage-StripAuth removes Authorization header |
| Apigee | Rate limiting | Spike Arrest: 500 req/s |
| Internal LB | Blocks direct internet access | VPC-internal only (no public IP) |
| CallerIdentityMiddleware | Blocks requests that bypassed Apigee | Rejects missing X-Caller-Identity in production |
| Pydantic validators | Prevents injection | Regex, length bounds, control-char check on all inputs |
| SQLAlchemy ORM | Prevents SQL injection | Parameterised queries only |
| SecurityHeadersMiddleware | Browser-level protection | CSP, HSTS, X-Frame-Options, nosniff |
| K8s pod spec | Container escape | Non-root user; `readOnlyRootFilesystem: true` |

---

## Resilience Patterns

```
Customer Service (external call)
  ├── Circuit breaker: opens after 5 consecutive failures
  │     Half-open after 30s — sends 1 probe request
  │     Closed again if probe succeeds
  │
  ├── Timeout: asyncio.wait_for(0.5s) — never blocks the thread
  │
  └── Graceful fallback: get_customer_or_none() returns None
        Caller decides whether None is acceptable or returns 404

GKE Deployment
  ├── HPA: min 3 replicas, scales on CPU
  ├── PodDisruptionBudget: ensures rolling update doesn't drop to 0
  └── Rollback: kubectl rollout undo on any deploy failure (CD pipeline)

Cloud SQL
  └── Pool settings: 5s connection timeout
        If Auth Proxy unreachable: pod logs "cloud-sql-proxy" container first
```

---

## Key URLs and Identifiers

| Item | Value |
|------|-------|
| Public base URL | `https://api.34-107-235-25.nip.io/v1/customer-service` |
| OAuth token endpoint | `POST /oauth/token` |
| Internal LB IP | `10.128.0.5:80` |
| GKE cluster | `ol-cluster`, region `us-central1` |
| Cloud SQL instance | `py-dev-ai`, PostgreSQL 18 |
| Docker image | `us-central1-docker.pkg.dev/pi-dev-ai-493823/pydevrepo/ol-service:<sha-tag>` |
| Apigee org | `pi-dev-ai-493823` |
| Apigee environment | `eval` |
| API Product | `customer-service-pi-product` |
| App client_id | `R6zubSdCmXh3yFzE0pv79OCBFRL1veTXo6jKywovhGkGp4nm` |

---

## Future State — GKE Gateway (not yet deployed)

Once a real domain is available, `k8s/gateway.yaml` and `k8s/httproute.yaml` will be applied.
The request path between the Internal LB and the pods will change:

```
Current:  Apigee → Internal LB (10.128.0.5) → Pod directly

Future:   Apigee → Internal LB → GKE Gateway (GatewayClass: gke-l7-rilb)
                                    └── HTTPRoute (path-based routing rules)
                                          └── Service → Pod
```

Benefits of GKE Gateway over direct Internal LB:
- Path-based routing (route `/v2/*` to a new deployment without Apigee changes)
- Traffic splitting (canary releases: 10% → new version, 90% → stable)
- Built-in 3s request / 2s backendRequest timeout enforced at the gateway level
- TLS between Apigee and GKE (end-to-end encryption)

Pre-requisite: a real registered domain. nip.io does not work with GKE Gateway's
managed certificate provisioning.
