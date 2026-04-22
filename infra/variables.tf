variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "pi-dev-ai-493823"
}

variable "region" {
  description = "Primary GCP region"
  type        = string
  default     = "us-central1"
}

variable "db_password" {
  description = "Password for the Cloud SQL application user (py-dev-usr)"
  type        = string
  sensitive   = true
  # Set via: export TF_VAR_db_password="..."
  # Or pass on the CLI: terraform apply -var="db_password=..."
}
