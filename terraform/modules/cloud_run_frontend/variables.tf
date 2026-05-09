variable "resource_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "region" {
  description = "GCP region."
  type        = string
}

variable "image" {
  description = "Full container image reference for the static-server image (Vite build)."
  type        = string
}

variable "service_account_email" {
  description = "Service account email for the Cloud Run service."
  type        = string
}

variable "backend_url" {
  description = "Backend Cloud Run service URL — injected as VITE_API_URL at runtime via env."
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
