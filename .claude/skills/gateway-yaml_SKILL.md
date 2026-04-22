---
name: gateway-yaml
description: >
  Write, update, or review Kubernetes Gateway API manifests for GKE.
  Use when creating or modifying GatewayClass, Gateway, HTTPRoute resources,
  configuring request timeouts, retry policies, traffic splitting,
  or replacing an existing Ingress resource with Gateway API.
allowed-tools: Read, Write, Bash(kubectl:*)
---

# Gateway API YAML — GKE

This service uses GKE Gateway API (not legacy Ingress). The full stack is:

```
Apigee → GKE Gateway (HTTPS:443) → HTTPRoute → OL Service ClusterIP → pods
```

Apigee handles: external auth, rate limiting, quota, spike arrest.
Gateway API handles: internal timeout, retry, traffic splitting, TLS termination.

---

## File locations

| File | Purpose |
|---|---|
| `k8s/gateway.yaml` | GatewayClass + Gateway resource |
| `k8s/httproute.yaml` | HTTPRoute with timeout, retry, weights |
| `k8s/service.yaml` | ClusterIP Service that HTTPRoute points to |

---

## GatewayClass + Gateway (`k8s/gateway.yaml`)

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
  name: ol-gateway-class
spec:
  controllerName: networking.gke.io/gateway
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: ol-gateway
  namespace: production
  annotations:
    networking.gke.io/certmap: ol-cert-map   # managed TLS via Certificate Manager
spec:
  gatewayClassName: ol-gateway-class
  listeners:
    - name: https
      protocol: HTTPS
      port: 443
      tls:
        mode: Terminate
        options:
          networking.gke.io/pre-shared-certs: ol-tls-cert
    - name: http                              # redirect HTTP → HTTPS
      protocol: HTTP
      port: 80
```

---

## HTTPRoute with timeout + retry (`k8s/httproute.yaml`)

This is the critical file for the holiday incident fix. Always include `timeouts`
and `retry` on the main backend rule.

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: ol-service-route
  namespace: production
spec:
  parentRefs:
    - name: ol-gateway
      namespace: production

  hostnames:
    - "api.your-domain.com"

  rules:
    # ── Health check route (no timeout restriction, no retry needed) ──
    - matches:
        - path:
            type: PathPrefix
            value: /health
      backendRefs:
        - name: ol-service
          port: 80
          weight: 100

    # ── Main API route ──
    - matches:
        - path:
            type: PathPrefix
            value: /
      timeouts:
        request: "3s"           # Total time client waits — after this, 504 returned
        backendRequest: "2s"    # Time OL service has to respond — must be < request
      filters:
        - type: ResponseHeaderModifier
          responseHeaderModifier:
            add:
              - name: X-Gateway
                value: gke-gateway-api
      backendRefs:
        - name: ol-service           # stable (production) backend
          port: 80
          weight: 90
        - name: ol-service-canary    # canary backend (set weight: 0 when not in use)
          port: 80
          weight: 10

      # Retry policy — retries on transient errors, NOT on all 5xx
      # (retrying 500 from bad request is wrong; retrying 503/504 is safe)
      retry:
        attempts: 2
        perTryTimeout: "1500ms"   # each individual attempt timeout
        retryOn:
          - "503"                 # Service Unavailable (pod not ready)
          - "504"                 # Gateway Timeout
          - "connection-error"    # TCP-level failure
          - "reset"               # Connection reset by peer
```

---

## ClusterIP Service (`k8s/service.yaml`)

```yaml
apiVersion: v1
kind: Service
metadata:
  name: ol-service
  namespace: production
  labels:
    app: ol-service
spec:
  type: ClusterIP
  selector:
    app: ol-service
  ports:
    - name: http
      protocol: TCP
      port: 80
      targetPort: 8000
---
# Canary service — points to canary deployment
apiVersion: v1
kind: Service
metadata:
  name: ol-service-canary
  namespace: production
spec:
  type: ClusterIP
  selector:
    app: ol-service
    track: canary             # canary pods must have this label
  ports:
    - port: 80
      targetPort: 8000
```

---

## Applying and verifying

```bash
kubectl apply -f k8s/gateway.yaml -n production
kubectl apply -f k8s/httproute.yaml -n production

# Verify Gateway is PROGRAMMED (not just Accepted)
kubectl get gateway ol-gateway -n production -o wide

# Verify HTTPRoute is Accepted and ResolvedRefs
kubectl describe httproute ol-service-route -n production

# Get external IP assigned to Gateway
kubectl get gateway ol-gateway -n production \
  -o jsonpath='{.status.addresses[0].value}'
```

Expected status on a healthy HTTPRoute:
```
Conditions:
  Type: Accepted   Status: True
  Type: ResolvedRefs  Status: True
```

---

## Adjusting timeout values

| Scenario | `request` | `backendRequest` |
|---|---|---|
| Normal operation | 3s | 2s |
| Bulk/export endpoints | 30s | 25s |
| Health checks | omit | omit |
| Search (Algolia heavy) | 5s | 4s |

For bulk endpoints, apply a separate HTTPRoute rule with a longer timeout matched
by path prefix (e.g. `/export`, `/bulk`).

---

## Traffic splitting for canary deploys

1. Deploy canary pods with label `track: canary`
2. Set `ol-service-canary` weight to `10` in HTTPRoute
3. Monitor error rates: `kubectl logs -n production -l track=canary`
4. Promote: change canary weight to `100`, stable weight to `0`
5. Update stable deployment, then set stable back to `100`, canary to `0`

---

## Common mistakes to avoid

- **Do not use `networking.k8s.io/v1` Ingress** — this project uses Gateway API only
- **`backendRequest` must always be less than `request`** — otherwise the backend
  timeout never fires and the gateway timeout kicks in first unexpectedly
- **Retry amplification:** never set `attempts` > 3 and never retry on `500` —
  retrying application errors will make an overloaded OL service worse, not better
- **HTTPRoute status "Accepted" does not mean traffic is flowing** — always check
  `ResolvedRefs` condition too; a misconfigured backend service name gives Accepted=True
  but drops all traffic silently
