###############################################################################
# Root module — wires the four resource modules together for a Demo SaaS deploy.
#
# Cost target at idle (us-central1, July 2026 list prices):
#
#   Cloud Run backend     scale to 0     → $0
#   Cloud Run frontend    scale to 0     → $0
#   Cloud SQL db-f1-micro 10 GB SSD      → ~$7 / month
#   Memorystore 1 GB BASIC               → ~$36 / month
#   Serverless VPC Connector             → ~$3 / month (min instances)
#   GCS state bucket                     → < $0.10 / month
#                                         ─────────────
#                                          ~$46 / month
#
# Idle cost budget: <$50/month. Cloud SQL + Memorystore dominate. The
# operator can drop Memorystore to a smaller tier or run a Redis container
# on Cloud Run for a sub-$15 demo, but BASIC 1 GB is the supported path.
###############################################################################

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

# ---------------------------------------------------------------------------
# Required APIs.
# ---------------------------------------------------------------------------

locals {
  required_services = [
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "redis.googleapis.com",
    "vpcaccess.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "compute.googleapis.com",
    "artifactregistry.googleapis.com",
    # v2.1 B5 — Cloud Scheduler drives the daily demo reset Job.
    "cloudscheduler.googleapis.com",
  ]

  labels = merge(var.common_labels, {
    env = var.env
  })

  resource_prefix = "${var.name_prefix}-${var.env}"
}

resource "google_project_service" "required" {
  for_each = toset(local.required_services)

  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false
}

# ---------------------------------------------------------------------------
# VPC + serverless connector.
# ---------------------------------------------------------------------------

resource "google_compute_network" "vpc" {
  name                    = "${local.resource_prefix}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"

  depends_on = [google_project_service.required]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${local.resource_prefix}-subnet"
  ip_cidr_range = var.vpc_cidr
  region        = var.region
  network       = google_compute_network.vpc.id

  private_ip_google_access = true
}

# Reserved range for Cloud SQL private services access.
resource "google_compute_global_address" "private_ip_alloc" {
  name          = "${local.resource_prefix}-psa-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_alloc.name]
}

resource "google_vpc_access_connector" "connector" {
  name          = "${local.resource_prefix}-vpcconn"
  region        = var.region
  ip_cidr_range = var.vpc_connector_cidr
  network       = google_compute_network.vpc.name

  min_instances = 2
  max_instances = 3
  machine_type  = "e2-micro"

  depends_on = [google_project_service.required]
}

# ---------------------------------------------------------------------------
# Secret Manager — DB password, app secret key, derived DATABASE_URL.
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "db_password" {
  secret_id = "${local.resource_prefix}-db-password"

  replication {
    auto {}
  }

  labels     = local.labels
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = var.db_password
}

resource "google_secret_manager_secret" "app_secret_key" {
  secret_id = "${local.resource_prefix}-secret-key"

  replication {
    auto {}
  }

  labels     = local.labels
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "app_secret_key" {
  secret      = google_secret_manager_secret.app_secret_key.id
  secret_data = var.app_secret_key
}

# ---------------------------------------------------------------------------
# Sub-modules.
# ---------------------------------------------------------------------------

module "cloud_sql" {
  source = "./modules/cloud_sql"

  resource_prefix = local.resource_prefix
  region          = var.region
  vpc_id          = google_compute_network.vpc.id
  tier            = var.db_tier
  disk_size_gb    = var.db_disk_size_gb
  db_password     = var.db_password
  labels          = local.labels

  depends_on = [
    google_service_networking_connection.private_vpc,
  ]
}

module "memorystore_redis" {
  source = "./modules/memorystore_redis"

  resource_prefix  = local.resource_prefix
  region           = var.region
  vpc_id           = google_compute_network.vpc.id
  memory_size_gb   = var.redis_memory_size_gb
  labels           = local.labels

  depends_on = [google_project_service.required]
}

# Service accounts for the two Cloud Run services. Least-privilege: each
# service can only read its own secrets and (backend only) connect to Cloud
# SQL via the Auth Proxy.
resource "google_service_account" "backend" {
  account_id   = "${local.resource_prefix}-backend"
  display_name = "TrustedOSS backend (${var.env})"

  depends_on = [google_project_service.required]
}

resource "google_service_account" "frontend" {
  account_id   = "${local.resource_prefix}-frontend"
  display_name = "TrustedOSS frontend (${var.env})"

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "backend_cloud_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_db_password" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_app_secret_key" {
  secret_id = google_secret_manager_secret.app_secret_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

module "cloud_run_backend" {
  source = "./modules/cloud_run_backend"

  resource_prefix         = local.resource_prefix
  region                  = var.region
  project_id              = var.project_id
  image                   = var.backend_image
  service_account_email   = google_service_account.backend.email
  vpc_connector           = google_vpc_access_connector.connector.id
  cloud_sql_instance      = module.cloud_sql.connection_name
  redis_host              = module.memorystore_redis.host
  redis_port              = module.memorystore_redis.port
  db_password_secret_id   = google_secret_manager_secret.db_password.secret_id
  app_secret_key_secret_id = google_secret_manager_secret.app_secret_key.secret_id
  db_user                 = module.cloud_sql.db_user
  db_name                 = module.cloud_sql.db_name
  min_instances           = var.cloud_run_min_instances
  max_instances           = var.cloud_run_max_instances
  demo_read_only          = var.demo_read_only
  labels                  = local.labels
}

module "cloud_run_frontend" {
  source = "./modules/cloud_run_frontend"

  resource_prefix       = local.resource_prefix
  region                = var.region
  image                 = var.frontend_image
  service_account_email = google_service_account.frontend.email
  backend_url           = module.cloud_run_backend.service_url
  min_instances         = var.cloud_run_min_instances
  max_instances         = var.cloud_run_max_instances
  labels                = local.labels
}

# ---------------------------------------------------------------------------
# v2.1 Track B (B5) — daily demo reset.
#
# RECOMMENDED Secret Manager secret holding a STABLE demo super-admin password
# so the published demo credentials survive the nightly reseed (security-reviewer
# M-2 — the recommended path so the Job never generates/logs a credential).
# Created only when `demo_super_admin_password` is supplied; otherwise the reset
# Job lets seed_demo generate a random one each night and, in the demo env, does
# NOT log the plaintext (only a masked advisory).
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "demo_super_admin_password" {
  count = var.demo_reset_enabled && var.demo_super_admin_password != "" ? 1 : 0

  secret_id = "${local.resource_prefix}-demo-admin-password"

  replication {
    auto {}
  }

  labels     = local.labels
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "demo_super_admin_password" {
  count = var.demo_reset_enabled && var.demo_super_admin_password != "" ? 1 : 0

  secret      = google_secret_manager_secret.demo_super_admin_password[0].id
  secret_data = var.demo_super_admin_password
}

resource "google_secret_manager_secret_iam_member" "backend_demo_admin_password" {
  count = var.demo_reset_enabled && var.demo_super_admin_password != "" ? 1 : 0

  secret_id = google_secret_manager_secret.demo_super_admin_password[0].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

module "demo_reset" {
  count  = var.demo_reset_enabled ? 1 : 0
  source = "./modules/demo_reset"

  resource_prefix          = local.resource_prefix
  region                   = var.region
  project_id               = var.project_id
  image                    = var.backend_image
  # Reuse the backend SA — it already has cloudsql.client + secretAccessor.
  service_account_email    = google_service_account.backend.email
  vpc_connector            = google_vpc_access_connector.connector.id
  cloud_sql_instance       = module.cloud_sql.connection_name
  redis_host               = module.memorystore_redis.host
  redis_port               = module.memorystore_redis.port
  db_password_secret_id    = google_secret_manager_secret.db_password.secret_id
  app_secret_key_secret_id = google_secret_manager_secret.app_secret_key.secret_id
  db_user                  = module.cloud_sql.db_user
  db_name                  = module.cloud_sql.db_name
  schedule                 = var.demo_reset_schedule
  time_zone                = var.demo_reset_time_zone
  demo_super_admin_password_secret_id = (
    var.demo_super_admin_password != ""
    ? google_secret_manager_secret.demo_super_admin_password[0].secret_id
    : ""
  )
  labels = local.labels
}
