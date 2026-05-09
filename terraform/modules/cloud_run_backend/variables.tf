variable "resource_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "region" {
  description = "GCP region."
  type        = string
}

variable "project_id" {
  description = "GCP project ID (used in env DATABASE_URL hostname when using Cloud SQL Auth Proxy)."
  type        = string
}

variable "image" {
  description = "Full container image reference (Artifact Registry path + tag)."
  type        = string
}

variable "service_account_email" {
  description = "Service account email for the Cloud Run service."
  type        = string
}

variable "vpc_connector" {
  description = "Serverless VPC Connector ID for reaching private Cloud SQL + Memorystore."
  type        = string
}

variable "cloud_sql_instance" {
  description = "Cloud SQL connection name (project:region:instance) for the Auth Proxy sidecar."
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

variable "min_instances" {
  description = "Cloud Run minimum instances."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Cloud Run maximum instances."
  type        = number
  default     = 3
}

variable "labels" {
  description = "Resource labels."
  type        = map(string)
  default     = {}
}
