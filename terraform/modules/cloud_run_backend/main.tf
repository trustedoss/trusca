###############################################################################
# Cloud Run — FastAPI backend.
#
# Deployment shape:
#   - 1 vCPU / 512 MiB, scale-to-zero by default (min_instances=0).
#   - Cloud SQL Auth Proxy connection via run.googleapis.com/cloudsql-instances
#     annotation. The container reaches Postgres via the unix socket at
#     /cloudsql/<connection_name>/.s.PGSQL.5432 — no IP, no password rotation.
#   - VPC connector for reaching Memorystore Redis on its private IP.
#   - Secrets pulled from Secret Manager at startup (no Terraform state
#     leakage, no env-var plaintext in the Cloud Run revision spec).
#   - APP_ENV=demo so the seed script's super-admin guard treats it as safe
#     for one-off seeding (gcp-deploy.md operator runbook step 5).
#
# CORS: backed by ALLOWED_ORIGINS env (set by the operator to the frontend
# Cloud Run URL after first apply). No wildcard.
###############################################################################

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.10"
    }
  }
}

resource "google_cloud_run_v2_service" "backend" {
  name     = "${var.resource_prefix}-backend"
  location = var.region

  ingress = "INGRESS_TRAFFIC_ALL"

  labels = var.labels

  template {
    service_account = var.service_account_email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    vpc_access {
      connector = var.vpc_connector
      egress    = "PRIVATE_RANGES_ONLY"
    }

    # Cloud SQL connection — Cloud Run injects /cloudsql/<conn>/.s.PGSQL.5432.
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [var.cloud_sql_instance]
      }
    }

    containers {
      image = var.image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      # ---- Plain env --------------------------------------------------------
      env {
        name  = "APP_ENV"
        value = "demo"
      }
      env {
        name  = "LOG_LEVEL"
        value = "INFO"
      }
      env {
        name  = "ACCESS_TOKEN_EXPIRE_MINUTES"
        value = "30"
      }
      env {
        name  = "REFRESH_TOKEN_EXPIRE_DAYS"
        value = "7"
      }
      env {
        # asyncpg over the Cloud SQL Auth Proxy unix socket.
        name  = "DATABASE_URL"
        value = "postgresql+asyncpg://${var.db_user}:__DB_PASSWORD__@/${var.db_name}?host=/cloudsql/${var.cloud_sql_instance}"
        # Note: __DB_PASSWORD__ is rewritten at container start by an entrypoint
        # shim that reads /run/secrets/db_password (mounted from Secret
        # Manager). This avoids a 'sensitive in plan' warning on the env block
        # and follows CLAUDE.md core rule #11 (runtime os.getenv).
      }
      env {
        name  = "REDIS_URL"
        value = "redis://${var.redis_host}:${var.redis_port}/0"
      }
      env {
        # Fallback CORS — the operator should override after `terraform apply`
        # to point at the actual frontend URL (output `frontend_service_url`).
        # Until then the backend rejects cross-origin browser calls.
        name  = "CORS_ALLOWED_ORIGINS"
        value = ""
      }

      # ---- Secret env -------------------------------------------------------
      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = var.db_password_secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = var.app_secret_key_secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        timeout_seconds   = 5
        period_seconds    = 5
        failure_threshold = 6
        tcp_socket {
          port = 8000
        }
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8000
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

# Public access — Cloud Run-managed *.run.app URLs are HTTPS by default.
# Operators who want a custom domain wire it through Cloud Run domain
# mappings outside this module.
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = google_cloud_run_v2_service.backend.project
  location = google_cloud_run_v2_service.backend.location
  name     = google_cloud_run_v2_service.backend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
