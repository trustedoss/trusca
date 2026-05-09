output "connection_name" {
  description = "Connection string used by the Cloud SQL Auth Proxy."
  value       = google_sql_database_instance.main.connection_name
}

output "instance_name" {
  description = "Cloud SQL instance short name."
  value       = google_sql_database_instance.main.name
}

output "private_ip" {
  description = "Private IPv4 of the instance (only reachable from the peered VPC)."
  value       = google_sql_database_instance.main.private_ip_address
  sensitive   = true
}

output "db_name" {
  description = "Application database name."
  value       = google_sql_database.app.name
}

output "db_user" {
  description = "Application database user."
  value       = google_sql_user.app.name
}
