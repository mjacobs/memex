"""Auth dependencies.

- /api/v1/*: bearer device key from settings().device_keys -> device_id.
- /internal/*: Google-signed OIDC token, audience settings().service_url
  (verification skipped when service_url is empty — local dev).
"""

import hmac
import logging

from fastapi import Request

from memex.api.common import ApiError
from memex.config import settings

logger = logging.getLogger(__name__)


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "unauthorized", "missing or malformed bearer token")
    return token.strip()


def require_device(request: Request) -> str:
    """FastAPI dependency: resolve the bearer key to a device_id."""
    token = _bearer_token(request)
    matched: str | None = None
    for device_id, key in settings().device_keys.items():
        # Constant-time compare, and check every key regardless of match, to
        # avoid a timing oracle on the only public gate.
        if hmac.compare_digest(key.encode(), token.encode()):
            matched = device_id
    if matched is not None:
        return matched
    raise ApiError(401, "unauthorized", "unknown device key")


def verify_internal(request: Request) -> dict:
    """FastAPI dependency for /internal/*: verify a Google-signed OIDC token.

    Two checks, both required (the service is public, so this is the only
    gate on /internal/*):
    - audience: the configured service URL OR the URL form this request
      actually arrived on (Cloud Run services answer on both the
      project-number and legacy run.app hostnames, and Eventarc mints its
      token for the legacy one; the Host header is GFE-routed, so it can
      only ever be one of our own hostnames);
    - caller identity: the token's verified email must be one of our
      invoker service accounts (Eventarc trigger / Cloud Scheduler).
    """
    cfg = settings()
    if not cfg.service_url:
        # Auth must never silently disappear because an env var went missing:
        # skipping verification requires the explicit local-dev opt-in.
        if cfg.insecure_local:
            return {}
        raise ApiError(
            503,
            "misconfigured",
            "MEMEX_SERVICE_URL is not set (set it, or MEMEX_INSECURE_LOCAL=1 "
            "for local dev)",
        )
    token = _bearer_token(request)
    if token.count(".") != 2:  # device keys / junk are not JWTs; skip cert fetch
        raise ApiError(401, "unauthorized", "expected a Google-signed OIDC token")
    allowed_audiences = {cfg.service_url, f"{cfg.service_url}{request.url.path}"}
    if request.url.hostname:
        # Pub/Sub (Eventarc) mints the token for the full push endpoint
        # (host + path, no query); Scheduler uses the bare service URL.
        base = f"https://{request.url.hostname}"
        allowed_audiences.update({base, f"{base}{request.url.path}"})
    try:
        from google.auth.transport import requests as ga_requests
        from google.oauth2 import id_token as google_id_token

        claims = google_id_token.verify_oauth2_token(
            token, ga_requests.Request(), audience=None
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning("OIDC verification failed: %s", exc)
        raise ApiError(401, "unauthorized", "OIDC verification failed") from exc
    if claims.get("aud") not in allowed_audiences:
        raise ApiError(401, "unauthorized", "OIDC audience mismatch")
    if not claims.get("email_verified") or claims.get("email") not in cfg.internal_invokers:
        raise ApiError(401, "unauthorized", "caller is not an allowed invoker")
    return claims
