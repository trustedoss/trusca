###############################################################################
# Root module outputs.
#
# These are the URLs / connection strings the operator needs after
# `terraform apply` to push images and run the seed script.
###############################################################################

output "backend_service_url" {
  description = "Public HTTPS URL of the Cloud Run backend (Cloud Run-managed *.run.app domain)."
  value       = module.cloud_run_backend.service_url
}

output "frontend_service_url" {
  description = "Public HTTPS URL of the Cloud Run frontend. Open this in a browser to verify."
  value       = module.cloud_run_frontend.service_url
}

output "cloud_sql_connection_name" {
  description = "Cloud SQL instance connection name (project:region:instance) for the Auth Proxy."
  value       = module.cloud_sql.connection_name
}

output "cloud_sql_private_ip" {
  description = "Cloud SQL private IP. Reachable only from inside the VPC."
  value       = module.cloud_sql.private_ip
  sensitive   = true
}

output "redis_host" {
  description = "Memorystore Redis private IP."
  value       = module.memorystore_redis.host
  sensitive   = true
}

output "redis_port" {
  description = "Memorystore Redis port (default 6379)."
  value       = module.memorystore_redis.port
}

output "db_name" {
  description = "Application database name."
  value       = module.cloud_sql.db_name
}

output "db_user" {
  description = "Application database user (read from Secret Manager for password)."
  value       = module.cloud_sql.db_user
}

output "db_password_secret_id" {
  description = "Secret Manager secret resource ID for the application DB password."
  value       = google_secret_manager_secret.db_password.id
  sensitive   = true
}

output "app_secret_key_secret_id" {
  description = "Secret Manager secret resource ID for SECRET_KEY."
  value       = google_secret_manager_secret.app_secret_key.id
  sensitive   = true
}
