variable "resource_prefix" {
  description = "Resource name prefix (e.g. 'trustedoss-demo')."
  type        = string
}

variable "region" {
  description = "GCP region for the Cloud SQL instance."
  type        = string
}

variable "vpc_id" {
  description = "VPC self-link or ID. Required for private IP allocation."
  type        = string
}

variable "tier" {
  description = "Cloud SQL machine tier. db-f1-micro for demo (~$7/mo)."
  type        = string
  default     = "db-f1-micro"
}

variable "disk_size_gb" {
  description = "Disk size in GB."
  type        = number
  default     = 10
}

variable "db_password" {
  description = "Initial password for the application DB user."
  type        = string
  sensitive   = true
}

variable "db_user_name" {
  description = "Application DB user name."
  type        = string
  default     = "trustedoss"
}

variable "db_name" {
  description = "Application database name."
  type        = string
  default     = "trustedoss"
}

variable "labels" {
  description = "Resource labels."
  type        = map(string)
  default     = {}
}
