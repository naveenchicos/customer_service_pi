# Daily Activity Guide

Quick reference for starting work, developing, deploying, and shutting down.

---

## One-Time Setup (do this once on a new machine)

```bash
# 1. Clone the repo
git clone https://github.com/naveenchicos/customer_service_pi.git
cd customer_service_pi

# 2. Set up Python virtual environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# 3. Add your DB password to ~/.zshrc so it's always available
echo 'export TF_VAR_db_password="your-db-password"' >> ~/.zshrc
source ~/.zshrc

# 4. Authenticate GCP tools
gcloud auth login
gcloud config set project pi-dev-ai-493823

# 5. Initialize Terraform (first time only)
cd infra && terraform init && cd ..
```

---

## Morning — Start Everything

```bash
./start.sh
```

What it does (in order):
1. Starts Cloud SQL — `activation_policy = ALWAYS`
2. Waits until SQL is RUNNABLE (~1–2 min)
3. Fetches GKE credentials
4. Scales `ol-service` pods to 3 replicas
5. Waits for rollout to complete

Total time: ~3–4 minutes.

---

## During the Day — Development Workflow

### Start a new feature or bug fix

```bash
# Always branch from develop
git checkout develop && git pull
git checkout -b feature/short-description
# or
git checkout -b bugfix/short-description
```

### Activate virtual environment (if not already active)

```bash
source .venv/bin/activate
```

### Run tests and lint before pushing

```bash
flake8 src/ tests/
black src/ tests/
mypy src/
pytest tests/unit/ -v
```

### Push and open a PR to develop

```bash
git push -u origin feature/short-description
gh pr create --base develop
```

CI will run automatically. Once it passes, get 1 approval and **squash merge** into `develop`.

---

## Deploy to Production

Cut a release branch from `develop` — CD does the rest.

```bash
git checkout develop && git pull
git checkout -b release/vX.Y.Z    # e.g. release/v1.2.0
git push origin release/vX.Y.Z
```

CD pipeline (automatic after push):
1. Run unit tests
2. Build and push Docker image (SHA tag)
3. Deploy to GKE production — **pauses for your manual approval in GitHub**
4. Smoke test (`/health` check)
5. Auto-merge `release/vX.Y.Z` → `main`, create tag `vX.Y.Z`

Go to [GitHub Actions](https://github.com/naveenchicos/customer_service_pi/actions) to approve the deploy.

---

## Urgent Fix (Hotfix)

For a bug already in production that cannot wait for the normal release cycle:

```bash
# Branch from main — not develop
git checkout main && git pull
git checkout -b hotfix/short-description

# Fix, commit, push, open PR to main
git push -u origin hotfix/short-description
gh pr create --base main

# After merging to main, immediately back-merge to develop
git checkout develop && git pull
git merge --no-ff origin/hotfix/short-description
git push origin develop
```

**Important:** always back-merge the hotfix into `develop` right away, otherwise the next release will conflict with `main`.

---

## Evening — Stop Everything

```bash
./stop.sh
```

What it does (in order):
1. Fetches GKE credentials
2. Scales `ol-service` pods to 0
3. Waits for all pods to terminate
4. Stops Cloud SQL — `activation_policy = NEVER`

Total time: ~2 minutes.

After stopping, you pay only for:
- Cloud SQL storage (~cents/day)
- GKE cluster control plane (~$0.10/hr, always running)
- Artifact Registry storage (~cents/month)

---

## Changing Region or Project

Edit `infra/config.env` — this is the only file to update for the scripts:

```bash
export TF_VAR_project_id="new-project-id"
export TF_VAR_region="us-east1"
export GKE_CLUSTER="new-cluster-name"
export SQL_INSTANCE="new-sql-instance-name"
export K8S_NAMESPACE="production"
export DEPLOYMENT="ol-service"
export REPLICAS=3
```

Also update the defaults in `infra/variables.tf` to match, then run `terraform init` again if the provider region changed.

---

## Quick Reference

| Task | Command |
|------|---------|
| Start infra | `./start.sh` |
| Stop infra | `./stop.sh` |
| Run unit tests | `pytest tests/unit/ -v` |
| Lint check | `flake8 src/ tests/` |
| Format check | `black --check src/ tests/` |
| Type check | `mypy src/` |
| New feature branch | `git checkout -b feature/name develop` |
| Open PR | `gh pr create --base develop` |
| Cut a release | `git checkout -b release/vX.Y.Z develop && git push origin release/vX.Y.Z` |
| Check pod status | `kubectl get pods -n production` |
| Check rollout | `kubectl rollout status deployment/ol-service -n production` |
| View pod logs | `kubectl logs -n production -l app=ol-service --tail=100` |
| SQL status | `gcloud sql instances describe py-dev-ai --format="value(state)"` |
| CI/CD runs | `gh run list --limit 10` |

---

## Troubleshooting

**`start.sh` — SQL never becomes RUNNABLE**
```bash
gcloud sql instances describe py-dev-ai --format="value(state,settings.activationPolicy)"
# If still STOPPED: try terraform apply manually
cd infra && terraform apply -var="sql_active=true" -target=google_sql_database_instance.py_dev_ai
```

**Pods crash-looping after start**
```bash
kubectl describe pod -n production -l app=ol-service
kubectl logs -n production -l app=ol-service -c ol-service --previous
# Most common cause: Cloud SQL not yet RUNNABLE when pods started
kubectl rollout restart deployment/ol-service -n production
```

**Circuit breaker open**
```bash
kubectl port-forward svc/ol-service 8080:80 -n production
curl http://localhost:8080/health/dependencies
# Check which downstream is open before restarting
```

**`TF_VAR_db_password` not set**
```bash
export TF_VAR_db_password="your-db-password"
# Or add permanently: echo 'export TF_VAR_db_password="..."' >> ~/.zshrc
```
