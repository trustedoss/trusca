###############################################################################
# Cloud SQL — PostgreSQL 17 (private IP, db-f1-micro, daily backup).
#
# Cost-conscious choices:
#   - tier = db-f1-micro  → ~$7/month at us-central1 (shared core).
#   - point_in_time_recovery_enabled = false → no WAL archiving cost.
#   - backup_configuration.enabled  = true   → daily automated backup retained 7 days.
#   - ipv4_enabled = false → no public IP (Cloud Run reaches via VPC peering).
###############################################################################

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.10"
    }
  }
}

resource "google_sql_database_instance" "main" {
  name             = "${var.resource_prefix}-pg17"
  database_version = "POSTGRES_17"
  region           = var.region

  # Demo SaaS — no production safety rails: deletion_protection=false so
  # `terraform destroy` works without manual override. Production deploys
  # use Helm + an external DB and never set this flag.
  deletion_protection = false

  settings {
    tier              = var.tier
    availability_type = "ZONAL" # ZONAL is cheaper than REGIONAL; demo SLA is not 99.95.
    disk_type         = "PD_SSD"
    disk_size         = var.disk_size_gb
    disk_autoresize   = true

    user_labels = var.labels

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = var.vpc_id
      enable_private_path_for_google_cloud_services = true
    }

    backup_configuration {
      enabled    = true
      start_time = "03:00" # UTC

      # Demo deliberately skips PITR — WAL archiving alone runs ~$1-2/month
      # but the storage compounds. Daily backup with 7-day retention is
      # adequate for a demo dataset that we can re-seed at any time.
      point_in_time_recovery_enabled = false
      transaction_log_retention_days = 1

      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }

    insights_config {
      query_insights_enabled  = true
      query_string_length     = 1024
      record_application_tags = false
      record_client_address   = false
    }

    maintenance_window {
      day          = 7 # Sunday
      hour         = 4
      update_track = "stable"
    }
  }
}

resource "google_sql_database" "app" {
  name     = var.db_name
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "app" {
  name     = var.db_user_name
  instance = google_sql_database_instance.main.name
  password = var.db_password
}
