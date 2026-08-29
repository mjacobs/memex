# Firestore native-mode database — the system of record.
resource "google_firestore_database" "default" {
  project     = var.project
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  # Personal-project posture: allow terraform destroy to tear it down.
  delete_protection_state = "DELETE_PROTECTION_DISABLED"
  deletion_policy         = "DELETE"

  depends_on = [google_project_service.enabled]
}

# Composite indexes for every filtered + ULID-ordered query in the
# contract. Firestore scans these backward too, so each serves both
# ascending (agent tools) and descending (API feed) order.
locals {
  eq_indexes = {
    tasks_status      = { collection = "tasks", field = "status", array = false }
    approvals_status  = { collection = "approvals", field = "status", array = false }
    notes_kind        = { collection = "notes", field = "kind", array = false }
    notes_tags        = { collection = "notes", field = "tags", array = true }
    operations_status = { collection = "operations", field = "status", array = false }
  }
}

resource "google_firestore_index" "eq_by_id" {
  # Both id directions: the agent tools list ascending, the API feed
  # descending, and Firestore requires an exact direction match on the
  # orderBy field.
  for_each = {
    for pair in setproduct(keys(local.eq_indexes), ["ASCENDING", "DESCENDING"]) :
    "${pair[0]}_${lower(pair[1])}" => merge(local.eq_indexes[pair[0]], { dir = pair[1] })
  }
  project    = var.project
  database   = google_firestore_database.default.name
  collection = each.value.collection

  fields {
    field_path   = each.value.field
    array_config = each.value.array ? "CONTAINS" : null
    order        = each.value.array ? null : "ASCENDING"
  }
  fields {
    field_path = "id"
    order      = each.value.dir
  }
}

resource "google_firestore_index" "notes_tag_kind" {
  # GET /notes?tag=X&kind=Y filters on both; array-contains + equality + id
  # needs its own composite (the feed only queries descending).
  project    = var.project
  database   = google_firestore_database.default.name
  collection = "notes"

  fields {
    field_path   = "tags"
    array_config = "CONTAINS"
  }
  fields {
    field_path = "kind"
    order      = "ASCENDING"
  }
  fields {
    field_path = "id"
    order      = "DESCENDING"
  }
}
