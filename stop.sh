#!/usr/bin/env bash
# Stop all infrastructure — scale pods to 0, then stop Cloud SQL.
# Run at the end of your work day.
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

# ── 1. Get GKE credentials ────────────────────────────────────────────────────
echo "==> Fetching GKE credentials ($GKE_CLUSTER, $TF_VAR_region)..."
gcloud container clusters get-credentials "$GKE_CLUSTER" \
  --region "$TF_VAR_region" \
  --project "$TF_VAR_project_id" \
  --quiet

# ── 2. Scale pods to 0 (must happen before SQL stops) ────────────────────────
echo "==> Scaling $DEPLOYMENT to 0 replicas..."
kubectl scale deployment/"$DEPLOYMENT" \
  --replicas=0 \
  -n "$K8S_NAMESPACE"

# Wait for all pods to terminate before cutting the database connection
kubectl wait pod \
  -n "$K8S_NAMESPACE" \
  -l app="$DEPLOYMENT" \
  --for=delete \
  --timeout=2m 2>/dev/null || true

# ── 3. Stop Cloud SQL ─────────────────────────────────────────────────────────
echo "==> Stopping Cloud SQL ($SQL_INSTANCE)..."
terraform -chdir="$REPO_ROOT/infra" apply \
  -var="sql_active=false" \
  -target=google_sql_database_instance.py_dev_ai \
  -auto-approve

echo ""
echo "Done. Infra is stopped."
echo "  SQL:  $SQL_INSTANCE (STOPPED — storage charges only)"
echo "  Pods: 0 (GKE cluster still running at ~\$0.10/hr)"
