# Cloud SQL PostgreSQL instance
#
# Import existing instance:
#   terraform import google_sql_database_instance.py_dev_ai \
#     projects/pi-dev-ai-493823/instances/py-dev-ai
#
# Import existing database:
#   terraform import google_sql_database.postgres \
#     projects/pi-dev-ai-493823/instances/py-dev-ai/databases/postgres
#
# Import existing user (password is not readable from GCP — Terraform will manage it going forward):
#   terraform import google_sql_user.app_user \
#     projects/pi-dev-ai-493823/instances/py-dev-ai/users/py-dev-usr

resource "google_sql_database_instance" "py_dev_ai" {
  name             = "py-dev-ai"
  region           = var.region
  database_version = "POSTGRES_18"

  # Prevent accidental deletion of the production database.
  deletion_protection = true

  settings {
    tier              = "db-perf-optimized-N-8"
    edition           = "ENTERPRISE_PLUS"
    availability_type = "ZONAL"
    activation_policy = var.sql_active ? "ALWAYS" : "NEVER"

    disk_type             = "PD_SSD"
    disk_size             = 100
    disk_autoresize       = false

    enable_dataplex_integration = true

    location_preference {
      zone = "us-central1-a"
    }

    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ALLOW_UNENCRYPTED_AND_ENCRYPTED"
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    backup_configuration {
      enabled                        = false
      point_in_time_recovery_enabled = false
      start_time                     = "16:00"
      backup_retention_settings {
        retained_backups = 15
        retention_unit   = "COUNT"
      }
    }

    data_cache_config {
      data_cache_enabled = true
    }
  }
}

resource "google_sql_database" "postgres" {
  name     = "postgres"
  instance = google_sql_database_instance.py_dev_ai.name
}

resource "google_sql_user" "app_user" {
  name     = "py-dev-usr"
  instance = google_sql_database_instance.py_dev_ai.name
  password = var.db_password
}
