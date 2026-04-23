# Apigee OAuth Proxy

OAuth 2.0 client credentials flow for Customer Service Pi.
Apigee issues and validates tokens — no OAuth service needed in the backend.

## Files

| File | Purpose |
|---|---|
| `proxy-endpoint.xml` | Full proxy flow wiring — shows policy execution order |
| `policies/OAuthV2-GenerateToken.xml` | Issues Bearer tokens on `POST /oauth/token` |
| `policies/OAuthV2-VerifyToken.xml` | Validates Bearer token on all other routes |
| `policies/AssignMessage-InjectCaller.xml` | Injects `X-Caller-Identity: <client_id>` after verification |
| `policies/AssignMessage-StripAuth.xml` | Removes `Authorization` header before forwarding to GKE |

---

## Request flow

```
Client
  │
  ├── POST /oauth/token  (grant_type=client_credentials + Basic auth)
  │     └── Apigee GenerateToken → returns { access_token, expires_in }
  │
  └── GET /accounts  (Authorization: Bearer <token>)
        └── Apigee VerifyToken
              └── InjectCaller  (sets X-Caller-Identity: <client_id>)
                    └── StripAuth  (removes Authorization header)
                          └── GKE → CallerIdentityMiddleware checks header → service
```

---

## One-time Apigee setup (UI)

### 1 — Create API Product

In **Publish → API Products → + Create**:

| Field | Value |
|---|---|
| Name | `customer-service-pi-product` |
| Display Name | `Customer Service Pi` |
| Access | `Private` |
| Environments | `prod` (or your env name) |
| Allowed OAuth Scopes | *(leave blank — client credentials flow doesn't use scopes here)* |
| API Proxies | Select your proxy |

### 2 — Create Developer

In **Publish → Developers → + Developer**:

| Field | Value |
|---|---|
| First / Last | any |
| Email | e.g. `platform@example.com` |

### 3 — Create App

In **Publish → Apps → + App**:

| Field | Value |
|---|---|
| Name | `customer-service-pi-app` |
| Developer | the developer from step 2 |
| Products | `customer-service-pi-product` |

After saving, Apigee shows the generated **`client_id`** and **`client_secret`**.
Store these — they replace the static `API_KEY` used by service consumers.

---

## Getting a token

```bash
CLIENT_ID="<from Apigee App>"
CLIENT_SECRET="<from Apigee App>"

curl -s -X POST https://<apigee-host>/v1/customer-service/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -u "$CLIENT_ID:$CLIENT_SECRET" \
  -d "grant_type=client_credentials" | jq .
```

Response:
```json
{
  "access_token": "abc123...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

## Calling a protected endpoint

```bash
TOKEN="<access_token from above>"

curl -s https://<apigee-host>/v1/customer-service/accounts \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Correlation-ID: my-trace-id" | jq .
```

---

## Token expiry

Tokens expire after **1 hour** (`ExpiresIn = 3600000ms` in `OAuthV2-GenerateToken.xml`).
Clients should request a new token when they receive a `401` or proactively before expiry.

---

## Security notes

- `Authorization` header is stripped by Apigee before reaching GKE — the Bearer token
  never appears in backend access logs.
- The backend `CallerIdentityMiddleware` requires `X-Caller-Identity` in production.
  Any request that bypasses Apigee will not have this header and will receive a `401`.
- `/health` is exempt from token validation in both Apigee (proxy condition) and the
  backend middleware (exempt path list).
