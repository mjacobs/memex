# Routine ticks. Each runs a full agent session inside the request on a
# scale-to-zero service, so give the attempt a generous deadline and a
# couple of retries (a daily job skipped is a whole day lost).
locals {
  routines = {
    daily_review = {
      schedule    = "0 9 * * *"
      description = "Daily task review: staleness, due dates, nags."
    }
    nightly_digest = {
      schedule    = "0 3 * * *"
      description = "Nightly digest: consolidate the day's notes."
    }
  }
}

resource "google_cloud_scheduler_job" "routine" {
  for_each = local.routines

  name        = "${var.service_name}-${each.key}"
  description = each.value.description
  project     = var.project
  region      = var.region
  schedule    = each.value.schedule
  time_zone   = var.time_zone

  attempt_deadline = "900s"

  retry_config {
    retry_count          = 2
    min_backoff_duration = "60s"
    max_backoff_duration = "600s"
  }

  http_target {
    http_method = "POST"
    uri         = "${local.service_url}/internal/routines/${each.key}/tick"

    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = local.service_url
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_cloud_run_v2_service_iam_member.scheduler_invoker,
  ]
}
