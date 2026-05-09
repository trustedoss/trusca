output "host" {
  description = "Private IPv4 of the Redis instance."
  value       = google_redis_instance.main.host
  sensitive   = true
}

output "port" {
  description = "Redis port (default 6379)."
  value       = google_redis_instance.main.port
}

output "instance_id" {
  description = "Redis instance resource ID."
  value       = google_redis_instance.main.id
}
