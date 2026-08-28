# GCS object-finalize on the capture buckets -> POST /internal/enrich.
# The finalize event's object name is captures/<capture_id>.<ext>.
resource "google_eventarc_trigger" "audio_finalize" {
  name     = "${var.service_name}-audio-finalize"
  project  = var.project
  location = var.region

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }

  matching_criteria {
    attribute = "bucket"
    value     = google_storage_bucket.audio.name
  }

  service_account = google_service_account.trigger.email

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.app.name
      region  = var.region
      path    = "/internal/enrich"
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_project_iam_member.gcs_pubsub_publisher,
    google_project_iam_member.trigger_event_receiver,
    google_cloud_run_v2_service_iam_member.trigger_invoker,
  ]
}

resource "google_eventarc_trigger" "images_finalize" {
  name     = "${var.service_name}-images-finalize"
  project  = var.project
  location = var.region

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }

  matching_criteria {
    attribute = "bucket"
    value     = google_storage_bucket.images.name
  }

  service_account = google_service_account.trigger.email

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.app.name
      region  = var.region
      path    = "/internal/enrich"
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_project_iam_member.gcs_pubsub_publisher,
    google_project_iam_member.trigger_event_receiver,
    google_cloud_run_v2_service_iam_member.trigger_invoker,
  ]
}
