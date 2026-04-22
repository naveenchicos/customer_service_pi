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

  # Autopilot manages addons, release channel, and VPA automatically.
  # Do not configure addons_config, release_channel, or vertical_pod_autoscaling
  # blocks — they conflict with enable_autopilot = true.

  release_channel {
    channel = "REGULAR"
  }
}
