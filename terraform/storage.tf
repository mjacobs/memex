# Capture blob bucket (audio and screenshots). Objects land at
# captures/<capture_id>.<ext>; the GCS finalize event drives the
# Eventarc -> /internal/enrich path, and that trigger is scoped to the one
# prefix, which is why images share the bucket.
resource "google_storage_bucket" "audio" {
  name     = "${var.project}-${var.service_name}-audio"
  project  = var.project
  location = var.region

  uniform_bucket_level_access = true
  force_destroy               = true

  # Raw audio is transient input: the transcript outlives the recording, so
  # the file goes after var.audio_retention_days. A screenshot is the
  # opposite — it is the note's content, and the note detail view loads it
  # back on every visit — so the rule matches audio suffixes only rather
  # than every object in the bucket.
  lifecycle_rule {
    condition {
      age            = var.audio_retention_days
      matches_suffix = [".m4a", ".wav", ".ogg", ".webm"]
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
