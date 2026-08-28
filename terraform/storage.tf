# Capture blob buckets. Objects land at captures/<capture_id>.<ext>; each
# bucket's GCS finalize event drives its own Eventarc -> /internal/enrich
# trigger. Audio and images are siblings so retention is a bucket-level
# policy instead of a suffix-matching rule that has to stay in sync with
# the code's extension tables.
resource "google_storage_bucket" "audio" {
  name     = "${var.project}-${var.service_name}-audio"
  project  = var.project
  location = var.region

  uniform_bucket_level_access = true
  force_destroy               = true

  # Raw audio is transient input: the transcript outlives the recording, so
  # the file goes after var.audio_retention_days. The suffix match protects
  # legacy screenshots uploaded here before the images bucket existed — a
  # screenshot is the note's content and must never age out.
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

# Screenshots are the note's content: no lifecycle rule, objects live until
# their note is deleted (the API cascades the delete).
resource "google_storage_bucket" "images" {
  name     = "${var.project}-${var.service_name}-images"
  project  = var.project
  location = var.region

  uniform_bucket_level_access = true
  force_destroy               = true

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
