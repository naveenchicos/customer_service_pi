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
gcloud auth configure-docker us-central1-docker.pkg.dev

# Build
docker build -t us-central1-docker.pkg.dev/PROJECT_ID/ol-repo/ol-service:TAG .

# Push
docker push us-central1-docker.pkg.dev/PROJECT_ID/ol-repo/ol-service:TAG
```

### Step 3 — Update deployment image tag
Edit `k8s/deployment.yaml`, update the image field:
```yaml
image: us-central1-docker.pkg.dev/PROJECT_ID/ol-repo/ol-service:TAG
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

## Important constraints

- Never run `kubectl delete` on production resources — use rollback or patch instead
- Never change the `production` namespace context without user confirmation
- Always check Gateway and HTTPRoute status after applying — a misconfigured
  HTTPRoute will silently drop traffic even if the deployment is healthy
- Cloud SQL Auth Proxy runs as a sidecar — if pods fail to start, check proxy logs:
  `kubectl logs POD_NAME -c cloud-sql-proxy -n production`
