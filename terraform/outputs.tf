output "service_url" {
  description = "Deterministic Cloud Run URL (also the OIDC audience)."
  value       = local.service_url
}

output "service_uri" {
  description = "URL reported by Cloud Run itself."
  value       = google_cloud_run_v2_service.app.uri
}

output "audio_bucket" {
  description = "GCS bucket for raw audio captures."
  value       = google_storage_bucket.audio.name
}

output "images_bucket" {
  description = "GCS bucket for screenshot captures (no expiry)."
  value       = google_storage_bucket.images.name
}

output "artifact_repo" {
  description = "Artifact Registry docker repo path for image pushes."
  value       = "${var.region}-docker.pkg.dev/${var.project}/${google_artifact_registry_repository.docker.repository_id}"
}

output "device_keys_secret" {
  description = "Secret Manager secret holding device bearer keys."
  value       = google_secret_manager_secret.device_keys.secret_id
}
