variable "project" {
  type        = string
  description = "GCP project id. Set it in terraform.tfvars (gitignored)."
}

variable "region" {
  type        = string
  description = "Region for Cloud Run, GCS, Eventarc, Scheduler, and Firestore."
  default     = "us-central1"
}

variable "service_name" {
  type        = string
  description = "Cloud Run service name."
  default     = "memex"
}

variable "image" {
  type        = string
  description = "Container image for the Cloud Run service (e.g. us-central1-docker.pkg.dev/YOUR_PROJECT_ID/memex/memex:latest)."
}

variable "model" {
  type        = string
  description = "Gemini model id for analysis (MEMEX_MODEL)."
  default     = "gemini-3.7-flash"
}

variable "transcribe_model" {
  type        = string
  description = "Gemini model id for audio transcription (MEMEX_TRANSCRIBE_MODEL)."
  default     = "gemini-3.5-flash-lite"
}

variable "vertex_location" {
  type        = string
  description = "Vertex location (MEMEX_VERTEX_LOCATION)."
  default     = "global"
}

variable "time_zone" {
  type        = string
  description = "Time zone for the Cloud Scheduler routine jobs."
  default     = "America/Los_Angeles"
}

variable "audio_retention_days" {
  type        = number
  description = "Days to keep raw audio objects in the capture bucket."
  default     = 30
}
