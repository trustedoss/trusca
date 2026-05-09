variable "resource_prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "region" {
  description = "GCP region."
  type        = string
}

variable "vpc_id" {
  description = "Authorized VPC self-link or ID for private IP."
  type        = string
}

variable "memory_size_gb" {
  description = "Redis instance size in GB."
  type        = number
  default     = 1
}

variable "labels" {
  description = "Resource labels."
  type        = map(string)
  default     = {}
}
