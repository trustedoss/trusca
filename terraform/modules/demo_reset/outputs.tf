output "job_name" {
  description = "Name of the Cloud Run Job that performs the daily demo reset."
  value       = google_cloud_run_v2_job.demo_reset.name
}

output "scheduler_job_name" {
  description = "Name of the Cloud Scheduler job that triggers the reset."
  value       = google_cloud_scheduler_job.demo_reset.name
}

output "scheduler_service_account_email" {
  description = "Service account the scheduler uses to invoke the Job."
  value       = google_service_account.scheduler.email
}
