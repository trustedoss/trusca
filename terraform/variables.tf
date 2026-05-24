###############################################################################
# Root module inputs.
#
# Everything that a fresh GCP project needs to stand up the Demo SaaS lives
# here. No defaults for sensitive values (db_password, secret_key) — the
# operator must populate terraform.tfvars or supply -var on the command line.
###############################################################################

variable "project_id" {
  description = "GCP project ID. Must already exist with billing enabled."
  type        = string

  validation {
    condition     = length(var.project_id) >= 6 && length(var.project_id) <= 30
    error_message = "GCP project_id must be 6-30 characters."
  }
}

variable "region" {
  description = "Primary GCP region for all regional resources (Cloud Run, Cloud SQL, Memorystore)."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Primary GCP zone (used by resources that require zonal placement)."
  type        = string
  default     = "us-central1-a"
}

variable "env" {
  description = "Deployment environment label. Used in resource names + the `env` resource label."
  type        = string
  default     = "demo"

  validation {
    condition     = contains(["demo", "staging"], var.env)
    error_message = "env must be one of: demo, staging. Production deploys go through Helm, not this Terraform module."
  }
}

variable "name_prefix" {
  description = "Prefix for all resource names. Keeps multiple deployments in the same project disambiguated."
  type        = string
  default     = "trustedoss"
}

# ---------------------------------------------------------------------------
# Backend image — published from CI to Artifact Registry. The deployment
# pipeline replaces ${IMAGE_TAG} with the GA tag (e.g. "2.0.0-rc1"). Default
# is intentionally a non-existent placeholder so a misconfigured apply fails
# closed.
# ---------------------------------------------------------------------------

variable "backend_image" {
  description = "Full container image reference for the FastAPI backend (Artifact Registry path + tag)."
  type        = string
  default     = "us-central1-docker.pkg.dev/PROJECT_ID/trustedoss/backend:0.0.0-placeholder"
}

variable "frontend_image" {
  description = "Full container image reference for the Vite static-server frontend."
  type        = string
  default     = "us-central1-docker.pkg.dev/PROJECT_ID/trustedoss/frontend:0.0.0-placeholder"
}

# ---------------------------------------------------------------------------
# Database — Cloud SQL for PostgreSQL 17.
# ---------------------------------------------------------------------------

variable "db_tier" {
  description = "Cloud SQL machine tier. db-f1-micro keeps the demo idle cost near $7/month."
  type        = string
  default     = "db-f1-micro"
}

variable "db_disk_size_gb" {
  description = "Cloud SQL disk size in GB. 10 GB is plenty for a demo dataset."
  type        = number
  default     = 10
}

variable "db_password" {
  description = "Initial password for the application DB user. Stored only in Secret Manager — Terraform state is encrypted at rest in GCS."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.db_password) >= 16
    error_message = "db_password must be at least 16 characters (Cloud SQL minimum + portal requirement)."
  }
}

# ---------------------------------------------------------------------------
# Application secrets.
# ---------------------------------------------------------------------------

variable "app_secret_key" {
  description = "FastAPI SECRET_KEY (used for JWT signing). 32+ random hex chars. Generate with: openssl rand -hex 32."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.app_secret_key) >= 32
    error_message = "app_secret_key must be at least 32 characters."
  }
}

# ---------------------------------------------------------------------------
# Memorystore Redis.
# ---------------------------------------------------------------------------

variable "redis_memory_size_gb" {
  description = "Memorystore Redis instance size in GB. 1 GB BASIC is the floor."
  type        = number
  default     = 1
}

# ---------------------------------------------------------------------------
# Network — Cloud Run uses a Serverless VPC Connector to reach Cloud SQL +
# Memorystore on private IPs. The VPC + subnet are created here.
# ---------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "Primary subnet CIDR for the demo VPC."
  type        = string
  default     = "10.20.0.0/24"
}

variable "vpc_connector_cidr" {
  description = "Serverless VPC Connector /28. Must not overlap vpc_cidr."
  type        = string
  default     = "10.20.1.0/28"
}

# ---------------------------------------------------------------------------
# Cost guards.
# ---------------------------------------------------------------------------

variable "cloud_run_min_instances" {
  description = "Cloud Run min instances. 0 = scale to zero (idle cost = 0)."
  type        = number
  default     = 0

  validation {
    condition     = var.cloud_run_min_instances >= 0 && var.cloud_run_min_instances <= 5
    error_message = "min_instances must be between 0 and 5 (cost guard)."
  }
}

variable "cloud_run_max_instances" {
  description = "Cloud Run max instances. Demo SaaS does not autoscale aggressively."
  type        = number
  default     = 3
}

variable "common_labels" {
  description = "Resource labels applied to every Terraform-managed resource that supports labels."
  type        = map(string)
  default = {
    app     = "trustedoss-portal"
    env     = "demo"
    managed = "terraform"
  }
}

# ---------------------------------------------------------------------------
# v2.1 Track B (B5) — live read-only demo + daily reset.
# ---------------------------------------------------------------------------

variable "demo_read_only" {
  description = <<-EOT
    Run the backend as a READ-ONLY live demo: the DemoReadOnlyMiddleware blocks
    every non-auth mutation with an RFC 7807 403, and GET /health reports the
    flag so the SPA shows a read-only banner. Recommended `true` for the public
    demo deploy.
  EOT
  type        = bool
  default     = true
}

variable "demo_reset_enabled" {
  description = "Provision the daily Cloud Scheduler → Cloud Run Job that drops + reseeds the demo dataset."
  type        = bool
  default     = true
}

variable "demo_reset_schedule" {
  description = "Cron schedule (Cloud Scheduler syntax) for the daily demo reset."
  type        = string
  default     = "17 3 * * *"
}

variable "demo_reset_time_zone" {
  description = "IANA time zone the demo reset schedule runs in."
  type        = string
  default     = "Etc/UTC"
}

variable "demo_super_admin_password" {
  description = <<-EOT
    RECOMMENDED stable password for the demo super-admin (security-reviewer
    M-2). When set it is stored in Secret Manager and the nightly reset Job
    reseeds with this KNOWN credential — the Job never generates or logs a
    password. Leave empty to have seed_demo generate a random one each run;
    note that in the demo env the plaintext is NOT logged (only a masked
    advisory), so you would not learn the credential. Must be ≥ 12 chars when
    set.
  EOT
  type        = string
  default     = ""
  sensitive   = true

  validation {
    condition     = var.demo_super_admin_password == "" || length(var.demo_super_admin_password) >= 12
    error_message = "demo_super_admin_password must be empty or at least 12 characters."
  }
}
