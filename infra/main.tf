terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Remote state in GCS — create the bucket once before running terraform init:
  #   gsutil mb -p pi-dev-ai-493823 -l us-central1 gs://pi-dev-ai-493823-tfstate
  #   gsutil versioning set on gs://pi-dev-ai-493823-tfstate
  backend "gcs" {
    bucket = "pi-dev-ai-493823-tfstate"
    prefix = "customer-service-pi"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
