# Durable operations queue: deep-research polling re-enqueues itself here
# against /internal/operations/poll (see docs/contracts.md). Names/location
# must match memex/config.py's tasks_queue/tasks_location defaults.
resource "google_cloud_tasks_queue" "operations" {
  name     = "memex-operations"
  project  = var.project
  location = var.region

  rate_limits {
    max_concurrent_dispatches = 10
    max_dispatches_per_second = 5
  }

  retry_config {
    max_attempts  = 240 # matches the poll handler's own attempt cap
    min_backoff   = "10s"
    max_backoff   = "300s"
    max_doublings = 5
  }

  depends_on = [google_project_service.enabled]
}
