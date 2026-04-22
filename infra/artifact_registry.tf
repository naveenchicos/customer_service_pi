# Artifact Registry Docker repository
#
# Import existing repo:
#   terraform import google_artifact_registry_repository.pydevrepo \
#     projects/pi-dev-ai-493823/locations/us-central1/repositories/pydevrepo

resource "google_artifact_registry_repository" "pydevrepo" {
  repository_id = "pydevrepo"
  location      = var.region
  format        = "DOCKER"
  description   = "Docker images for OL service"
}
