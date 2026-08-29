"""Runtime configuration — all deploy-varying values enter here via env."""

import json
import os
from functools import lru_cache

from pydantic import BaseModel, Field


def _env(name: str, default: str = ""):
    # default_factory so the env is read when settings() is (re)built, not
    # baked in at import time (tests monkeypatch env + cache_clear).
    return Field(default_factory=lambda: os.environ.get(name, default))


class Settings(BaseModel):
    project: str = _env("GOOGLE_CLOUD_PROJECT", "m4tt-xyz")
    location: str = _env("MEMEX_VERTEX_LOCATION", "global")
    # Analysis model (text enrichment + routine sessions); the promo-priced
    # 3.7 flash is newer and cheaper than 3.5 flash through 2026-12-31.
    model: str = _env("MEMEX_MODEL", "gemini-3.7-flash")
    # Transcription-heavy audio enrichment rides the cheaper/faster lite tier.
    transcribe_model: str = _env("MEMEX_TRANSCRIBE_MODEL", "gemini-3.5-flash-lite")
    audio_bucket: str = _env("MEMEX_AUDIO_BUCKET")
    # Empty means "use audio_bucket" (see gcs.py) so code can deploy ahead of
    # the terraform that creates the sibling bucket.
    images_bucket: str = _env("MEMEX_IMAGES_BUCKET")
    # {"<device_id>": "<key>"}; prod loads from Secret Manager into this env
    device_keys: dict[str, str] = {}
    service_url: str = _env("MEMEX_SERVICE_URL")  # OIDC audience
    # Cloud Tasks queue for durable operations (deep-research polling).
    tasks_queue: str = _env("MEMEX_TASKS_QUEUE", "memex-operations")
    tasks_location: str = _env("MEMEX_TASKS_LOCATION", "us-central1")
    # Service account whose OIDC token Cloud Tasks attaches when calling
    # /internal/operations/poll (audience = service_url). Reuses the
    # scheduler invoker SA, which internal_invokers below already trusts.
    tasks_invoker_sa: str = Field(
        default_factory=lambda: os.environ.get(
            "MEMEX_TASKS_INVOKER_SA",
            f"memex-scheduler@{os.environ.get('GOOGLE_CLOUD_PROJECT', 'm4tt-xyz')}"
            ".iam.gserviceaccount.com",
        )
    )
    # Explicit opt-in to skip /internal OIDC verification (local dev only).
    insecure_local: bool = Field(
        default_factory=lambda: os.environ.get("MEMEX_INSECURE_LOCAL", "") == "1"
    )
    # Service accounts allowed to call /internal/* (Eventarc trigger,
    # Cloud Scheduler). Comma-separated env override.
    internal_invokers: tuple[str, ...] = Field(
        default_factory=lambda: tuple(
            v.strip()
            for v in os.environ.get(
                "MEMEX_INTERNAL_INVOKERS",
                ",".join(
                    f"memex-{sa}@{os.environ.get('GOOGLE_CLOUD_PROJECT', 'm4tt-xyz')}"
                    ".iam.gserviceaccount.com"
                    for sa in ("trigger", "scheduler")
                ),
            ).split(",")
            if v.strip()
        )
    )


@lru_cache
def settings() -> Settings:
    keys = json.loads(os.environ.get("MEMEX_DEVICE_KEYS_JSON", "{}"))
    return Settings(device_keys=keys)
