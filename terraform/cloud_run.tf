resource "google_cloud_run_v2_service" "app" {
  name     = var.service_name
  location = var.region
  project  = var.project

  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.run.email

    # Audio enrichment and routine turns run inside the request; give them
    # room beyond the 300s default.
    timeout = "900s"

    scaling {
      # Scale-to-zero is a hard project invariant — never raise this.
      min_instance_count = 0
      max_instance_count = 4
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project
      }

      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "True"
      }

      env {
        name  = "MEMEX_VERTEX_LOCATION"
        value = var.vertex_location
      }

      env {
        name  = "MEMEX_MODEL"
        value = var.model
      }

      env {
        name  = "MEMEX_AUDIO_BUCKET"
        value = google_storage_bucket.audio.name
      }

      # OIDC audience for /internal/* verification (deterministic URL,
      # see main.tf).
      env {
        name  = "MEMEX_SERVICE_URL"
        value = local.service_url
      }

      env {
        name = "MEMEX_DEVICE_KEYS_JSON"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.device_keys.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_project_iam_member.run_datastore,
    google_project_iam_member.run_aiplatform,
    google_storage_bucket_iam_member.run_audio_object_admin,
    google_secret_manager_secret_iam_member.run_device_keys,
  ]

  # Image rollouts happen via `gcloud run deploy` / `make deploy`; keep
  # terraform from reverting them on the next apply.
  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].containers[0].image,
    ]
  }
}
