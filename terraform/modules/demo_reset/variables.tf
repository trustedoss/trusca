variable "resource_prefix" {
  description = "Resource name prefix (e.g. trustedoss-demo)."
  type        = string
}

variable "region" {
  description = "GCP region."
  type        = string
}

variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "image" {
  description = "Backend container image reference (same image the backend service runs)."
  type        = string
}

variable "service_account_email" {
  description = <<-EOT
    Service account the reset Job runs as. Reuse the backend SA so it already
    has roles/cloudsql.client + secretAccessor on the DB password / SECRET_KEY.
  EOT
  type        = string
}

variable "vpc_connector" {
  description = "Serverless VPC Connector ID for reaching private Cloud SQL + Memorystore."
  type        = string
}

variable "cloud_sql_instance" {
  description = "Cloud SQL connection name (project:region:instance) for the Auth Proxy."
  type        = string
}

variable "redis_host" {
  description = "Redis private IP."
  type        = string
}

variable "redis_port" {
  description = "Redis port."
  type        = number
  default     = 6379
}

variable "db_password_secret_id" {
  description = "Secret Manager secret ID (short name) for the DB password."
  type        = string
}

variable "app_secret_key_secret_id" {
  description = "Secret Manager secret ID (short name) for SECRET_KEY."
  type        = string
}

variable "db_user" {
  description = "Application DB user."
  type        = string
}

variable "db_name" {
  description = "Application DB name."
  type        = string
}

variable "schedule" {
  description = <<-EOT
    Cron schedule (Cloud Scheduler syntax) for the daily reset. Default 03:17
    to avoid the top-of-hour thundering herd. Interpreted in `time_zone`.
  EOT
  type        = string
  default     = "17 3 * * *"
}

variable "time_zone" {
  description = "IANA time zone the schedule is evaluated in."
  type        = string
  default     = "Etc/UTC"
}

variable "demo_super_admin_password_secret_id" {
  description = <<-EOT
    RECOMMENDED Secret Manager secret ID for DEMO_SUPER_ADMIN_PASSWORD
    (security-reviewer M-2). When set, the reset Job reseeds the demo
    super-admin with a STABLE, KNOWN password (so the public demo credentials
    do not change every night) and the Job never generates or logs a
    credential. When empty, seed_demo generates a random password each run but
    does NOT log the plaintext in the demo env (only a masked advisory event);
    provision this secret so you actually know the published credential.
  EOT
  type        = string
  default     = ""
}

variable "labels" {
  description = "Resource labels."
  type        = map(string)
  default     = {}
}
