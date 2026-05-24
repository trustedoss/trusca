###############################################################################
# Demo daily reset — v2.1 Track B (B5).
#
# Cloud Scheduler → Cloud Run Job → `python -m scripts.reset_demo`.
#
# The Job runs the SAME backend image as the Cloud Run service, with the same
# Cloud SQL Auth Proxy volume and the same DB / Redis / secret env wiring, but
# overrides the container command to run the scoped drop+reseed. `reset_demo`:
#   * refuses unless APP_ENV ∈ {dev, demo} (it is `demo` here),
#   * deletes ONLY the demo-org (FK cascade) + demo users (@demo.trustedoss.dev),
#   * then reseeds via the idempotent seed_demo._seed.
#
# Cost: a Cloud Run Job costs nothing at rest; one short execution per day is
# well within the free tier. No min instances, no always-on scheduler cost
# beyond the (free-tier) Cloud Scheduler job itself.
#
# DEPLOY/APPLY is the operator lane (O2) — this file only declares the IaC.
###############################################################################

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.10"
    }
  }
}

# ---------------------------------------------------------------------------
# Cloud Run Job — runs scripts/reset_demo.py against Cloud SQL.
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_job" "demo_reset" {
  name     = "${var.resource_prefix}-demo-reset"
  location = var.region

  labels = var.labels

  template {
    template {
      service_account = var.service_account_email

      # The reset touches the DB once and exits; give it a generous deadline
      # but no retries beyond the default so a broken migration fails loud.
      timeout     = "600s"
      max_retries = 1

      vpc_access {
        connector = var.vpc_connector
        egress    = "PRIVATE_RANGES_ONLY"
      }

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [var.cloud_sql_instance]
        }
      }

      containers {
        image = var.image

        # Override the service CMD (uvicorn) — run the reset module and exit.
        command = ["python", "-m", "scripts.reset_demo"]

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }

        # APP_ENV=demo so the reset/seed env guard permits the run. Anything
        # else (prod/test/unset) makes reset_demo refuse with exit 1.
        env {
          name  = "APP_ENV"
          value = "demo"
        }
        env {
          name  = "LOG_LEVEL"
          value = "INFO"
        }

        # DB connection parts — composed at runtime by core.config.database_url
        # exactly like the backend service (DB_PASSWORD from Secret Manager).
        env {
          name  = "DB_USER"
          value = var.db_user
        }
        env {
          name  = "DB_HOST"
          value = "/cloudsql/${var.cloud_sql_instance}"
        }
        env {
          name  = "DB_NAME"
          value = var.db_name
        }
        env {
          name  = "REDIS_URL"
          value = "redis://${var.redis_host}:${var.redis_port}/0"
        }

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

        # RECOMMENDED — a Secret Manager-backed, stable demo super-admin
        # password (security-reviewer M-2). When set, the daily reset Job
        # reseeds with a KNOWN credential and never has to generate or surface
        # one. When unset, seed_demo generates a random password but — in the
        # demo env — does NOT log the plaintext (only a masked advisory), so
        # the operator must set this secret to know the published credential.
        dynamic "env" {
          for_each = var.demo_super_admin_password_secret_id == "" ? [] : [1]
          content {
            name = "DEMO_SUPER_ADMIN_PASSWORD"
            value_source {
              secret_key_ref {
                secret  = var.demo_super_admin_password_secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Scheduler invoker SA + IAM — least privilege to RUN the Job only.
# ---------------------------------------------------------------------------

resource "google_service_account" "scheduler" {
  account_id   = "${var.resource_prefix}-reset-sched"
  display_name = "TrustedOSS demo reset scheduler"
}

# The scheduler SA may invoke (run) the Job, nothing else.
resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  project  = google_cloud_run_v2_job.demo_reset.project
  location = google_cloud_run_v2_job.demo_reset.location
  name     = google_cloud_run_v2_job.demo_reset.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

# ---------------------------------------------------------------------------
# Cloud Scheduler — daily trigger via the Cloud Run Admin API `:run` endpoint.
# ---------------------------------------------------------------------------

resource "google_cloud_scheduler_job" "demo_reset" {
  name      = "${var.resource_prefix}-demo-reset"
  region    = var.region
  schedule  = var.schedule
  time_zone = var.time_zone

  # Give a daily job room to be late without overlapping the next day.
  attempt_deadline = "320s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri = format(
      "https://%s-run.googleapis.com/v2/projects/%s/locations/%s/jobs/%s:run",
      var.region,
      var.project_id,
      var.region,
      google_cloud_run_v2_job.demo_reset.name,
    )

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }
}
