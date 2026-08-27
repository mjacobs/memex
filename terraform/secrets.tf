# Device bearer keys: JSON {"<device_id>": "<key>", ...}. The secret resource
# is managed here, but versions are added out of band so keys never touch
# terraform state:
#
#   echo -n '{"dev": "some-long-random-key"}' | \
#     gcloud secrets versions add memex-device-keys --project m4tt-xyz --data-file=-
resource "google_secret_manager_secret" "device_keys" {
  project   = var.project
  secret_id = "memex-device-keys"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}
