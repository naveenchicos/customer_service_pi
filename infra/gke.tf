# GKE Autopilot cluster
#
# Import existing cluster:
#   terraform import google_container_cluster.ol_cluster \
#     projects/pi-dev-ai-493823/locations/us-central1/clusters/ol-cluster

resource "google_container_cluster" "ol_cluster" {
  name     = "ol-cluster"
  location = var.region

  enable_autopilot = true

  network    = "default"
  subnetwork = "default"

  # Autopilot manages the node pool — no node_pool blocks needed.
  # deletion_protection prevents accidental `terraform destroy` in production.
  deletion_protection = true

  addons_config {
    dns_cache_config {
      enabled = true
    }
    gce_persistent_disk_csi_driver_config {
      enabled = true
    }
    gcs_fuse_csi_driver_config {
      enabled = true
    }
  }

  release_channel {
    channel = "REGULAR"
  }

  vertical_pod_autoscaling {
    enabled = true
  }
}
