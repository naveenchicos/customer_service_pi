output "gke_cluster_name" {
  description = "GKE cluster name"
  value       = google_container_cluster.ol_cluster.name
}

output "gke_cluster_endpoint" {
  description = "GKE cluster API endpoint"
  value       = google_container_cluster.ol_cluster.endpoint
  sensitive   = true
}

output "cloudsql_connection_name" {
  description = "Cloud SQL connection name for Auth Proxy (PROJECT:REGION:INSTANCE)"
  value       = google_sql_database_instance.py_dev_ai.connection_name
}

output "cloudsql_public_ip" {
  description = "Cloud SQL public IP address"
  value       = google_sql_database_instance.py_dev_ai.public_ip_address
}

output "artifact_registry_url" {
  description = "Full Artifact Registry URL for Docker images"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.pydevrepo.repository_id}"
}

output "service_account_email" {
  description = "Service account email used by the app and CI/CD"
  value       = google_service_account.dev_engineer.email
}
