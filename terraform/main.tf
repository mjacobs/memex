terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
  }
}

provider "google" {
  project = var.project
  region  = var.region
}

locals {
  required_services = [
    "run.googleapis.com",
    "firestore.googleapis.com",
    "eventarc.googleapis.com",
    "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com",
    "aiplatform.googleapis.com",
    "storage.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "pubsub.googleapis.com",
    "cloudtasks.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each = toset(local.required_services)

  project            = var.project
  service            = each.key
  disable_on_destroy = false
}

data "google_project" "current" {
  project_id = var.project
}

# Deterministic Cloud Run URL (project-number format). Used as the OIDC
# audience and as the service's own MEMEX_SERVICE_URL env var — referencing
# google_cloud_run_v2_service.app.uri there would be a self-reference cycle.
locals {
  service_url = "https://${var.service_name}-${data.google_project.current.number}.${var.region}.run.app"
}
