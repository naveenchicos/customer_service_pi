# Infrastructure — Terraform

Codifies the GCP resources for Customer Service Pi:

| File | Resources |
|---|---|
| `main.tf` | Provider, GCS backend |
| `variables.tf` | `project_id`, `region`, `db_password` |
| `gke.tf` | GKE Autopilot cluster (`ol-cluster`) |
| `cloudsql.tf` | Cloud SQL instance, database, user |
| `artifact_registry.tf` | Artifact Registry Docker repo (`pydevrepo`) |
| `iam.tf` | Service account + IAM role bindings |
| `outputs.tf` | Connection names, URLs, emails |

---

## First-time setup (resources already exist in GCP)

### Step 1 — Create the Terraform state bucket

```bash
gsutil mb -p pi-dev-ai-493823 -l us-central1 gs://pi-dev-ai-493823-tfstate
gsutil versioning set on gs://pi-dev-ai-493823-tfstate
```

### Step 2 — Initialise

```bash
cd infra/
terraform init
```

### Step 3 — Import existing resources

Run each import command to bring existing GCP resources under Terraform management
without destroying and recreating them:

> **Permission requirements:** Some imports require elevated GCP roles.
> The identity running Terraform needs:
>
> - `roles/compute.viewer` (or `container.admin`) for the GKE cluster import
> - `roles/iam.serviceAccountAdmin` for the service account import
> - `roles/resourcemanager.projectIamAdmin` + Cloud Resource Manager API enabled for IAM binding imports
>
> If these permissions are unavailable, skip those imports and let `terraform apply` create the
> resources — **but never apply GKE cluster creation against an existing cluster**; it will be
> skipped if already present or will error. Import the cluster first when permissions allow.

```bash
# Cloud SQL instance
terraform import google_sql_database_instance.py_dev_ai \
  projects/pi-dev-ai-493823/instances/py-dev-ai

# Cloud SQL database
terraform import google_sql_database.postgres \
  projects/pi-dev-ai-493823/instances/py-dev-ai/databases/postgres

# Cloud SQL user (password not readable from GCP — Terraform manages it going forward)
terraform import google_sql_user.app_user \
  projects/pi-dev-ai-493823/instances/py-dev-ai/users/py-dev-usr

# Artifact Registry
terraform import google_artifact_registry_repository.pydevrepo \
  projects/pi-dev-ai-493823/locations/us-central1/repositories/pydevrepo

# GKE cluster (requires roles/compute.viewer or higher)
terraform import google_container_cluster.ol_cluster \
  projects/pi-dev-ai-493823/locations/us-central1/clusters/ol-cluster

# Service account (requires roles/iam.serviceAccountAdmin)
terraform import google_service_account.dev_engineer \
  projects/pi-dev-ai-493823/serviceAccounts/pi-ai-dev-engineer@pi-dev-ai-493823.iam.gserviceaccount.com

# IAM bindings (requires Cloud Resource Manager API + roles/resourcemanager.projectIamAdmin)
terraform import google_project_iam_member.dev_engineer_artifact_writer \
  "pi-dev-ai-493823 roles/artifactregistry.writer serviceAccount:pi-ai-dev-engineer@pi-dev-ai-493823.iam.gserviceaccount.com"

terraform import google_project_iam_member.dev_engineer_cloudsql_editor \
  "pi-dev-ai-493823 roles/cloudsql.editor serviceAccount:pi-ai-dev-engineer@pi-dev-ai-493823.iam.gserviceaccount.com"

terraform import google_project_iam_member.dev_engineer_container_developer \
  "pi-dev-ai-493823 roles/container.developer serviceAccount:pi-ai-dev-engineer@pi-dev-ai-493823.iam.gserviceaccount.com"
```

### Step 4 — Plan (should show no changes after import)

```bash
export TF_VAR_db_password="pydevusr"
terraform plan
```

If `terraform plan` shows changes after import, review each one carefully before applying —
changes to `deletion_protection`, disk size, or tier affect a live production database.

### Step 5 — Apply

```bash
terraform apply
```

---

## Day-to-day usage

```bash
# Preview changes
terraform plan

# Apply changes
terraform apply

# Show current state
terraform show

# View outputs
terraform output
terraform output cloudsql_connection_name
```

---

## Sensitive values

- `db_password` is marked `sensitive = true` — never appears in plan/apply output
- Set it via environment variable to avoid shell history:
  ```bash
  export TF_VAR_db_password="your-password"
  ```
- `gke_cluster_endpoint` output is also sensitive

---

## Important

- `deletion_protection = true` is set on both the GKE cluster and Cloud SQL instance.
  To destroy them you must first set it to `false` and apply, then destroy.
- Never run `terraform destroy` on production without explicit approval.
