"""Runtime configuration — all deploy-varying values enter here via env."""

import json
import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    project: str = os.environ.get("GOOGLE_CLOUD_PROJECT", "m4tt-xyz")
    location: str = os.environ.get("MEMEX_VERTEX_LOCATION", "global")
    model: str = os.environ.get("MEMEX_MODEL", "gemini-3.5-flash")
    audio_bucket: str = os.environ.get("MEMEX_AUDIO_BUCKET", "")
    # {"<device_id>": "<key>"}; prod loads from Secret Manager into this env
    device_keys: dict[str, str] = {}
    service_url: str = os.environ.get("MEMEX_SERVICE_URL", "")  # OIDC audience


@lru_cache
def settings() -> Settings:
    keys = json.loads(os.environ.get("MEMEX_DEVICE_KEYS_JSON", "{}"))
    return Settings(device_keys=keys)
