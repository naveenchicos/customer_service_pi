# Service account used by the application and CI/CD pipeline.
#
# Import existing SA:
#   terraform import google_service_account.dev_engineer \
#     projects/pi-dev-ai-493823/serviceAccounts/pi-ai-dev-engineer@pi-dev-ai-493823.iam.gserviceaccount.com
#
# IAM bindings — import each individually:
#   terraform import google_project_iam_member.dev_engineer_artifact_admin \
#     "pi-dev-ai-493823 roles/artifactregistry.admin serviceAccount:pi-ai-dev-engineer@pi-dev-ai-493823.iam.gserviceaccount.com"
#   terraform import google_project_iam_member.dev_engineer_cloudsql_editor \
#     "pi-dev-ai-493823 roles/cloudsql.editor serviceAccount:pi-ai-dev-engineer@pi-dev-ai-493823.iam.gserviceaccount.com"
#   terraform import google_project_iam_member.dev_engineer_container_admin \
#     "pi-dev-ai-493823 roles/container.admin serviceAccount:pi-ai-dev-engineer@pi-dev-ai-493823.iam.gserviceaccount.com"

resource "google_service_account" "dev_engineer" {
  account_id   = "pi-ai-dev-engineer"
  display_name = "PI AI Dev Engineer"
  description  = "Used by the application pods (Cloud SQL Auth Proxy) and CI/CD pipeline"
}

# Deploy images to Artifact Registry
resource "google_project_iam_member" "dev_engineer_artifact_admin" {
  project = var.project_id
  role    = "roles/artifactregistry.admin"
  member  = "serviceAccount:${google_service_account.dev_engineer.email}"
}

# Connect to Cloud SQL via Auth Proxy
resource "google_project_iam_member" "dev_engineer_cloudsql_editor" {
  project = var.project_id
  role    = "roles/cloudsql.editor"
  member  = "serviceAccount:${google_service_account.dev_engineer.email}"
}

# kubectl set image / rollout status from CI/CD
resource "google_project_iam_member" "dev_engineer_container_admin" {
  project = var.project_id
  role    = "roles/container.admin"
  member  = "serviceAccount:${google_service_account.dev_engineer.email}"
}
