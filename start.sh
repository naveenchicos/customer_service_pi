#!/usr/bin/env bash
# Start all infrastructure and scale up the service.
# Run once in the morning before starting work.
#
# Requires:
#   export TF_VAR_db_password="your-db-password"
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$REPO_ROOT/infra/config.env"

# ── Guard ─────────────────────────────────────────────────────────────────────
if [[ -z "${TF_VAR_db_password:-}" ]]; then
  echo "ERROR: TF_VAR_db_password is not set."
  echo "  export TF_VAR_db_password='your-db-password'"
  exit 1
fi

# ── 1. Start Cloud SQL ────────────────────────────────────────────────────────
echo "==> Starting Cloud SQL ($SQL_INSTANCE)..."
terraform -chdir="$REPO_ROOT/infra" apply \
  -var="sql_active=true" \
  -target=google_sql_database_instance.py_dev_ai \
  -auto-approve

# ── 2. Wait for Cloud SQL to be RUNNABLE ──────────────────────────────────────
echo "==> Waiting for Cloud SQL to be ready..."
for i in $(seq 1 24); do
  STATE=$(gcloud sql instances describe "$SQL_INSTANCE" \
    --project="$TF_VAR_project_id" \
    --format="value(state)" 2>/dev/null || echo "UNKNOWN")
  echo "    [$i/24] state: $STATE"
  if [[ "$STATE" == "RUNNABLE" ]]; then
    echo "    Cloud SQL is RUNNABLE"
    break
  fi
  if [[ $i -eq 24 ]]; then
    echo "ERROR: Cloud SQL did not become RUNNABLE after 4 minutes."
    exit 1
  fi
  sleep 10
done

# ── 3. Get GKE credentials ────────────────────────────────────────────────────
echo "==> Fetching GKE credentials ($GKE_CLUSTER, $TF_VAR_region)..."
gcloud container clusters get-credentials "$GKE_CLUSTER" \
  --region "$TF_VAR_region" \
  --project "$TF_VAR_project_id" \
  --quiet

# ── 4. Scale up pods ──────────────────────────────────────────────────────────
echo "==> Scaling $DEPLOYMENT to $REPLICAS replicas..."
kubectl scale deployment/"$DEPLOYMENT" \
  --replicas="$REPLICAS" \
  -n "$K8S_NAMESPACE"

kubectl rollout status deployment/"$DEPLOYMENT" \
  -n "$K8S_NAMESPACE" \
  --timeout=5m

echo ""
echo "Done. Service is up."
echo "  SQL:  $SQL_INSTANCE (RUNNABLE)"
echo "  Pods: $REPLICAS replicas in $K8S_NAMESPACE"
