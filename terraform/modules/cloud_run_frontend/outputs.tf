output "service_url" {
  description = "Cloud Run-managed HTTPS URL for the frontend."
  value       = google_cloud_run_v2_service.frontend.uri
}

output "service_name" {
  description = "Cloud Run service name."
  value       = google_cloud_run_v2_service.frontend.name
}
