###############################################################################
# Memorystore Redis — BASIC tier, 1 GB, private IP only.
#
# Cost: ~$36/month for BASIC 1 GB at us-central1 (July 2026 list price).
# This is the dominant idle cost of the demo. Operators wanting <$15/month
# can run a redis container on Cloud Run instead, but BASIC is the supported
# path because Cloud Run's serverless networking has zero connection-pool
# headroom and the VPC connector overhead favors a managed instance.
#
# Tier choices not used here:
#   - STANDARD_HA — adds HA replica, ~$72/mo. Not needed for demo.
#   - Cluster mode — overkill for a demo with 1 user.
###############################################################################

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.10"
    }
  }
}

resource "google_redis_instance" "main" {
  name           = "${var.resource_prefix}-redis"
  tier           = "BASIC"
  memory_size_gb = var.memory_size_gb
  region         = var.region

  redis_version = "REDIS_7_2"

  # AUTH disabled: BASIC + private VPC only, so the network already gates
  # access. Enabling AUTH is fine but adds another secret to wire through
  # Cloud Run env — saves nothing for the demo threat model.
  auth_enabled = false

  # Private IP via VPC peering. The VPC must already have a serverless
  # connector for Cloud Run to reach this private range.
  authorized_network = var.vpc_id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"

  redis_configs = {
    maxmemory-policy = "allkeys-lru"
  }

  labels = var.labels
}
