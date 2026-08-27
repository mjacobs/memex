"""Auth dependencies.

- /api/v1/*: bearer device key from settings().device_keys -> device_id.
- /internal/*: Google-signed OIDC token, audience settings().service_url
  (verification skipped when service_url is empty — local dev).
"""

from fastapi import Request

from memex.api.common import ApiError
from memex.config import settings


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "unauthorized", "missing or malformed bearer token")
    return token.strip()


def require_device(request: Request) -> str:
    """FastAPI dependency: resolve the bearer key to a device_id."""
    token = _bearer_token(request)
    for device_id, key in settings().device_keys.items():
        if key == token:
            return device_id
    raise ApiError(401, "unauthorized", "unknown device key")


def verify_internal(request: Request) -> dict:
    """FastAPI dependency for /internal/*: verify a Google-signed OIDC token."""
    audience = settings().service_url
    if not audience:  # local dev
        return {}
    token = _bearer_token(request)
    try:
        from google.auth.transport import requests as ga_requests
        from google.oauth2 import id_token as google_id_token

        return google_id_token.verify_oauth2_token(
            token, ga_requests.Request(), audience=audience
        )
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(401, "unauthorized", f"OIDC verification failed: {exc}") from exc
