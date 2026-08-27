# Audio capture bucket. Objects land at captures/<capture_id>.<ext>; the
# GCS finalize event drives the Eventarc -> /internal/enrich path. Raw audio
# is transient input — delete after var.audio_retention_days.
resource "google_storage_bucket" "audio" {
  name     = "${var.project}-${var.service_name}-audio"
  project  = var.project
  location = var.region

  uniform_bucket_level_access = true
  force_destroy               = true

  lifecycle_rule {
    condition {
      age = var.audio_retention_days
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.enabled]
}

# Artifact Registry docker repo for the service image.
resource "google_artifact_registry_repository" "docker" {
  project       = var.project
  location      = var.region
  repository_id = var.service_name
  format        = "DOCKER"
  description   = "memex Cloud Run images"

  depends_on = [google_project_service.enabled]
}
