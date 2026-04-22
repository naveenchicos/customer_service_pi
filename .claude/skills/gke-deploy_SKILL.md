---
name: gke-deploy
description: >
  Deploy OL Service to GKE using Docker, Artifact Registry, and kubectl.
  Use when deploying a new version, updating k8s manifests, rolling back,
  or checking rollout status. Covers build, push, apply, and verify steps.
allowed-tools: Bash(docker:*), Bash(kubectl:*), Bash(gcloud:*), Bash(make:*), Read, Glob
---

# GKE Deploy — OL Service

## Pre-flight checks (always run first)

```bash
# 1. Confirm you are on the right cluster and namespace
kubectl config current-context
kubectl config get-contexts

# 2. For production deploys — confirm explicitly with the user before proceeding
# NEVER deploy to production namespace without user confirmation

# 3. Check current running version
kubectl get deployment ol-service -n production -o jsonpath='{.spec.template.spec.containers[0].image}'
```

---

## Standard deploy (build → push → apply)

### Step 1 — Run tests
```bash
make test-unit
# If tests fail, STOP. Do not deploy. Report failures to user.
```

### Step 2 — Build and push image
```bash
# Authenticate to Artifact Registry (if needed)
# NOTE: if docker build fails with "docker-credential-gcr not found", check
# ~/.docker/config.json — change "https://index.docker.io/v1/": "gcr" to "desktop"
gcloud auth configure-docker us-central1-docker.pkg.dev

# Build (repo name is pydevrepo — not ol-repo)
docker build -t us-central1-docker.pkg.dev/pi-dev-ai-493823/pydevrepo/ol-service:TAG .

# Push
docker push us-central1-docker.pkg.dev/pi-dev-ai-493823/pydevrepo/ol-service:TAG
```

### Step 3 — Update deployment image tag
Edit `k8s/deployment.yaml`, update the image field:
```yaml
image: us-central1-docker.pkg.dev/pi-dev-ai-493823/pydevrepo/ol-service:TAG
```

### Step 4 — Apply manifests
```bash
# Apply all (preferred — keeps Gateway + HTTPRoute in sync)
kubectl apply -f k8s/ -n production

# Or apply only the deployment
kubectl apply -f k8s/deployment.yaml -n production
```

### Step 5 — Verify rollout
```bash
kubectl rollout status deployment/ol-service -n production --timeout=3m

# Check pods are running and ready
kubectl get pods -n production -l app=ol-service

# Check Gateway and HTTPRoute are accepted
kubectl get gateway -n production
kubectl get httproute -n production

# Tail logs for first 60s after deploy
kubectl logs -n production -l app=ol-service --since=60s -f
```

### Step 6 — Smoke test
```bash
# Health check (replace HOST with your Gateway external IP)
curl -s https://HOST/health | jq .
curl -s https://HOST/health/dependencies | jq .
# dependencies endpoint reports Redis, Customer Service, Algolia circuit breaker states
```

---

## Rollback

```bash
# Immediate rollback to previous version
kubectl rollout undo deployment/ol-service -n production

# Rollback to a specific revision
kubectl rollout history deployment/ol-service -n production
kubectl rollout undo deployment/ol-service -n production --to-revision=N

# Verify rollback
kubectl rollout status deployment/ol-service -n production
```

---

## Pre-scale for peak traffic (holiday / campaign)

```bash
# Pre-scale before a known traffic spike — do NOT wait for HPA
kubectl scale deployment/ol-service --replicas=15 -n production

# After peak, scale back down (HPA will take over)
kubectl scale deployment/ol-service --replicas=3 -n production
```

---

## Staging deploy

Same steps but use `-n staging` namespace. No user confirmation required for staging.
Staging cluster context: `gke_PROJECT_staging`

---

## Useful debug commands

```bash
# Describe a crashing pod
kubectl describe pod POD_NAME -n production

# Get events (shows OOMKilled, scheduling issues)
kubectl get events -n production --sort-by='.lastTimestamp'

# Check resource usage
kubectl top pods -n production -l app=ol-service

# Check HPA status
kubectl get hpa -n production

# Port-forward for local debugging (bypasses Gateway)
kubectl port-forward svc/ol-service 8080:80 -n production
```

---

## Running Alembic migrations on GKE

Never run migrations from your laptop against Cloud SQL directly. Run inside a pod:

```bash
POD=$(kubectl get pod -n production -l app=ol-service -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n production $POD -c ol-service -- alembic upgrade head
kubectl exec -n production $POD -c ol-service -- alembic current
```

---

## Cloud SQL Auth Proxy — authentication

The proxy uses Application Default Credentials (ADC). On GKE without Workload Identity,
ADC resolves to the **node compute SA** which has no Cloud SQL permissions.

**Symptom:** `403 NOT_AUTHORIZED: missing permission cloudsql.instances.get`

**Diagnosis:**
```bash
kubectl logs POD_NAME -c cloud-sql-proxy -n production | tail -20
# Look for "403" or "NOT_AUTHORIZED" — means wrong credentials, not a network issue
```

**Fix (SA key mount — current approach):**
The deployment mounts a SA key from K8s Secret `cloud-sql-sa-key`.
If the secret is missing, recreate it:
```bash
kubectl create secret generic cloud-sql-sa-key \
  --from-file=key.json=/path/to/pi-dev-ai-493823-3001ad400aed.json \
  -n production
```
The proxy container must have:
```yaml
env:
  - name: GOOGLE_APPLICATION_CREDENTIALS
    value: /secrets/cloudsql/key.json
volumeMounts:
  - name: cloud-sql-sa-key
    mountPath: /secrets/cloudsql
    readOnly: true
```

**Future fix (Workload Identity — requires project owner):**
```bash
gcloud iam workload-identity-pools create "github-pool" \
  --project="pi-dev-ai-493823" --location="global"
gcloud iam workload-identity-pools providers create-oidc "github-provider" \
  --project="pi-dev-ai-493823" --location="global" \
  --workload-identity-pool="github-pool" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com"
```

---

## CD pipeline smoke test — selecting the right pod

After a rollout, old pods enter Terminating → Failed state briefly. Naively selecting
`{.items[0].metadata.name}` can return a Failed pod, causing `kubectl exec` to fail with
`cannot exec into a container in a completed pod`.

**Always select the newest Running pod:**
```bash
kubectl wait pod -n production -l app=ol-service \
  --for=condition=Ready --timeout=60s
POD=$(kubectl get pod -n production -l app=ol-service \
  --field-selector=status.phase=Running \
  --sort-by=.metadata.creationTimestamp \
  -o jsonpath='{.items[-1].metadata.name}')
kubectl exec -n production "$POD" -c ol-service -- \
  python3 -c "import urllib.request,json; print(json.loads(urllib.request.urlopen('http://localhost:8000/health').read())['status'])"
```

---

## Gateway — domain requirement

`k8s/gateway.yaml` provisions a GCP HTTPS load balancer with managed TLS.
**Do not apply without a real registered domain.** Google's certificate provisioning
requires a public DNS A record pointing to the load balancer IP. Fake/local domains will
leave the Gateway in a permanent broken state.

Apply sequence (once domain is ready):
```bash
kubectl apply -f k8s/gateway.yaml -n production
# Wait for load balancer IP
kubectl get gateway ol-gateway -n production -o jsonpath='{.status.addresses[0].value}'
# Point your domain's A record to that IP, then:
kubectl apply -f k8s/httproute.yaml -n production
kubectl get httproute -n production  # check ResolvedRefs = True
```

---

## Important constraints

- Never run `kubectl delete` on production resources — use rollback or patch instead
- Never change the `production` namespace context without user confirmation
- Always check Gateway and HTTPRoute status after applying — a misconfigured
  HTTPRoute will silently drop traffic even if the deployment is healthy
- Cloud SQL Auth Proxy runs as a sidecar — if pods fail to start, check proxy logs:
  `kubectl logs POD_NAME -c cloud-sql-proxy -n production`
- GKE Gateway requires a real domain — do not apply with placeholder hostnames
