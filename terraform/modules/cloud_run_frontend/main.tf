###############################################################################
# Cloud Run — frontend.
#
# The frontend image is a slim nginx (or `serve`) wrapper around the Vite
# production bundle (`dist/`). It serves static assets and reverse-proxies
# /api -> the backend Cloud Run URL via a small nginx config baked into the
# image. The backend URL is supplied via env so the same image works for
# demo / staging.
###############################################################################

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.10"
    }
  }
}

resource "google_cloud_run_v2_service" "frontend" {
  name     = "${var.resource_prefix}-frontend"
  location = var.region

  ingress = "INGRESS_TRAFFIC_ALL"

  labels = var.labels

  template {
    service_account = var.service_account_email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "256Mi"
        }
        cpu_idle          = true
        startup_cpu_boost = false
      }

      env {
        name  = "BACKEND_URL"
        value = var.backend_url
      }
      env {
        name  = "PORT"
        value = "8080"
      }

      startup_probe {
        timeout_seconds   = 3
        period_seconds    = 3
        failure_threshold = 5
        tcp_socket {
          port = 8080
        }
      }

      liveness_probe {
        http_get {
          path = "/"
          port = 8080
        }
        period_seconds    = 30
        timeout_seconds   = 5
        failure_threshold = 3
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = google_cloud_run_v2_service.frontend.project
  location = google_cloud_run_v2_service.frontend.location
  name     = google_cloud_run_v2_service.frontend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
