"""/internal routes: Eventarc GCS-finalize enrich + Cloud Scheduler ticks.

OIDC-verified (see auth.verify_internal); delegates to the W3 agent seam.
"""

from typing import get_args

import anyio.to_thread
from fastapi import APIRouter, Depends, Request

from memex.api.auth import verify_internal
from memex.api.common import ApiError
from memex.models import Capture, RoutineName
from memex.store import firestore as store

router = APIRouter(prefix="/internal", dependencies=[Depends(verify_internal)])


def _object_name(payload: dict) -> str:
    """Extract the GCS object name from a binary- or structured-mode CloudEvent."""
    if isinstance(payload.get("name"), str):
        return payload["name"]
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("name"), str):
        return data["name"]
    raise ApiError(400, "bad_event", "CloudEvent payload has no GCS object name")


@router.post("/enrich")
async def enrich(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ApiError(400, "bad_event", "request body is not JSON") from exc
    name = _object_name(payload)
    prefix, _, filename = name.partition("/")
    if prefix != "captures" or not filename:
        return {"status": "ignored", "object": name}
    capture_id = filename.rsplit(".", 1)[0]
    capture = store.get(Capture, capture_id)
    if capture is None:
        raise ApiError(404, "not_found", f"no capture for object {name}")
    try:
        from memex.agent.service import enrich_capture
    except ImportError as exc:
        raise ApiError(
            503, "agent_unavailable", "enrichment agent is not available"
        ) from exc
    # enrich_capture is synchronous (GCS + Gemini + Firestore); keep it off
    # the event loop so /health and other requests stay responsive.
    return await anyio.to_thread.run_sync(enrich_capture, capture_id)


@router.post("/routines/{routine}/tick")
def tick(routine: str) -> dict:
    if routine not in get_args(RoutineName):
        raise ApiError(404, "unknown_routine", f"unknown routine: {routine}")
    try:
        from memex.agent.service import run_routine
    except ImportError as exc:
        raise ApiError(
            503, "agent_unavailable", "routine agent is not available"
        ) from exc
    return run_routine(routine)
