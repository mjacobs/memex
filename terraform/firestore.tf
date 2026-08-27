# Firestore native-mode database — the system of record.
resource "google_firestore_database" "default" {
  project     = var.project
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  # Hackathon posture: allow terraform destroy to tear it down.
  delete_protection_state = "DELETE_PROTECTION_DISABLED"
  deletion_policy         = "DELETE"

  depends_on = [google_project_service.enabled]
}
