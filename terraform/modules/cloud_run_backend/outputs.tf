output "service_url" {
  description = "Cloud Run-managed HTTPS URL for the backend."
  value       = google_cloud_run_v2_service.backend.uri
}

output "service_name" {
  description = "Cloud Run service name."
  value       = google_cloud_run_v2_service.backend.name
}
