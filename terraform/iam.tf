# --- Service accounts -------------------------------------------------------

# Runtime identity for the Cloud Run service.
resource "google_service_account" "run" {
  project      = var.project
  account_id   = "${var.service_name}-run"
  display_name = "memex Cloud Run runtime"
}

# Identity Eventarc uses to deliver GCS finalize events to /internal/enrich.
resource "google_service_account" "trigger" {
  project      = var.project
  account_id   = "${var.service_name}-trigger"
  display_name = "memex Eventarc trigger"
}

# Identity Cloud Scheduler uses for the routine tick jobs.
resource "google_service_account" "scheduler" {
  project      = var.project
  account_id   = "${var.service_name}-scheduler"
  display_name = "memex Cloud Scheduler invoker"
}

# --- Runtime SA grants ------------------------------------------------------

resource "google_project_iam_member" "run_datastore" {
  project = var.project
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.run.email}"
}

resource "google_project_iam_member" "run_aiplatform" {
  project = var.project
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.run.email}"
}

# Bucket-scoped, not project-wide: the app only touches the capture buckets.
resource "google_storage_bucket_iam_member" "run_audio_object_admin" {
  bucket = google_storage_bucket.audio.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.run.email}"
}

resource "google_storage_bucket_iam_member" "run_images_object_admin" {
  bucket = google_storage_bucket.images.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.run.email}"
}

resource "google_secret_manager_secret_iam_member" "run_device_keys" {
  project   = var.project
  secret_id = google_secret_manager_secret.device_keys.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run.email}"
}

# --- Eventarc trigger grants ------------------------------------------------

resource "google_project_iam_member" "trigger_event_receiver" {
  project = var.project
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${google_service_account.trigger.email}"
}

resource "google_cloud_run_v2_service_iam_member" "trigger_invoker" {
  project  = var.project
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.trigger.email}"
}

# GCS publishes finalize events through Pub/Sub; its service agent needs
# publisher on the project for Eventarc GCS triggers to work.
data "google_storage_project_service_account" "gcs" {
  project = var.project
}

resource "google_project_iam_member" "gcs_pubsub_publisher" {
  project = var.project
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

# --- Scheduler grants -------------------------------------------------------

resource "google_cloud_run_v2_service_iam_member" "scheduler_invoker" {
  project  = var.project
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

# --- Public ingress ---------------------------------------------------------

# allUsers may invoke: the app does bearer-key auth itself (no IAP), and
# /internal/* verifies Google-signed OIDC tokens in-app.
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  project  = var.project
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
