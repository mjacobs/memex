"""/api/v1 routes per docs/contracts.md. All Firestore access via the store."""

from datetime import datetime
from typing import get_args

import anyio.to_thread
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ValidationError

from memex.api import gcs
from memex.api.auth import require_device
from memex.api.common import ApiError, dump
from memex.ids import new_ulid
from memex.models import (
    Approval,
    Capture,
    CaptureSource,
    Note,
    RoutineRun,
    Task,
    TaskCreateAction,
    TaskStatus,
    TaskUpdateAction,
)
from memex.store import firestore as store

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_device)])

AUDIO_EXTENSIONS = {
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "audio/webm": "webm",
}


def _source(value: str | None) -> str:
    return value if value in get_args(CaptureSource) else "api"


def _enrich(capture_id: str) -> dict:
    """Delegate to the W3 seam; 503 if the agent package isn't available."""
    try:
        from memex.agent.service import enrich_capture
    except ImportError as exc:
        raise ApiError(
            503, "agent_unavailable", "enrichment agent is not available"
        ) from exc
    return enrich_capture(capture_id)


class CaptureIn(BaseModel):
    text: str
    source: str | None = None


@router.post("/capture", status_code=201)
def capture_text(body: CaptureIn, device_id: str = Depends(require_device)) -> dict:
    if not body.text.strip():
        raise ApiError(400, "empty_text", "text must be non-empty")
    capture = Capture(
        id=new_ulid(),
        created_at=store.now(),
        source=_source(body.source),
        device_id=device_id,
        kind="text",
        text=body.text,
        status="pending",
    )
    store.put(capture)
    try:
        result = _enrich(capture.id)
    except ApiError:
        raise
    except Exception as exc:
        store.update(Capture, capture.id, {"status": "failed", "error": str(exc)})
        raise ApiError(502, "enrichment_failed", str(exc)) from exc
    if result.get("error"):
        # enrich_capture reports failures in the result rather than raising.
        raise ApiError(502, "enrichment_failed", result["error"])
    capture = store.get(Capture, capture.id) or capture
    note = store.get(Note, capture.note_id) if capture.note_id else None
    tasks: list[Task] = []
    if note:
        tasks = [t for tid in note.task_ids if (t := store.get(Task, tid))]
    return {
        "capture": dump(capture),
        "note": dump(note) if note else None,
        "tasks": [dump(t) for t in tasks],
    }


@router.post("/capture/audio", status_code=202)
async def capture_audio(
    request: Request, device_id: str = Depends(require_device)
) -> dict:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    ext = AUDIO_EXTENSIONS.get(content_type)
    if ext is None:
        raise ApiError(
            415,
            "unsupported_media_type",
            f"unsupported audio content-type: {content_type or '(none)'}",
        )
    data = await request.body()
    if not data:
        raise ApiError(400, "empty_body", "audio body must be non-empty")
    if len(data) > 25 * 1024 * 1024:
        raise ApiError(413, "payload_too_large", "audio body exceeds 25 MiB")
    capture_id = new_ulid()
    # Capture doc first: the GCS finalize event races Eventarc against this
    # write, and /internal/enrich 404s if the doc isn't there yet.
    capture = Capture(
        id=capture_id,
        created_at=store.now(),
        source=_source(request.headers.get("x-memex-source")),
        device_id=device_id,
        kind="audio",
        audio_gcs_uri=gcs.audio_uri(capture_id, ext),
        audio_mime=content_type,
        status="pending",
    )
    await anyio.to_thread.run_sync(store.put, capture)
    try:
        await anyio.to_thread.run_sync(
            gcs.upload_audio, capture_id, ext, data, content_type
        )
    except Exception as exc:
        store.update(Capture, capture_id, {"status": "failed", "error": str(exc)})
        raise ApiError(502, "upload_failed", "audio upload failed") from exc
    return {"id": capture_id}


@router.get("/captures/{capture_id}")
def get_capture(capture_id: str) -> dict:
    capture = store.get(Capture, capture_id)
    if capture is None:
        raise ApiError(404, "not_found", f"capture {capture_id} not found")
    return {"capture": dump(capture)}


@router.get("/notes")
def list_notes(
    limit: int = 50,
    before: str | None = None,
    tag: str | None = None,
    kind: str | None = None,
) -> dict:
    filters: list[tuple[str, str, object]] = []
    if tag:
        filters.append(("tags", "array_contains", tag))
    if kind:
        filters.append(("kind", "==", kind))
    notes = store.query(Note, filters=filters, limit=limit, before=before)
    return {"notes": [dump(n, include_trace=False) for n in notes]}


@router.get("/notes/{note_id}")
def get_note(note_id: str) -> dict:
    note = store.get(Note, note_id)
    if note is None:
        raise ApiError(404, "not_found", f"note {note_id} not found")
    return {"note": dump(note)}


@router.get("/tasks")
def list_tasks(status: str = "open") -> dict:
    if status not in get_args(TaskStatus):
        raise ApiError(400, "invalid_status", f"invalid task status: {status}")
    tasks = store.query(Task, filters=[("status", "==", status)], limit=200)
    return {"tasks": [dump(t) for t in tasks]}


class TaskPatch(BaseModel):
    status: TaskStatus | None = None
    title: str | None = None
    due_at: datetime | None = None
    tags: list[str] | None = None


def _apply_task_changes(task_id: str, changes: dict) -> Task:
    task = store.get(Task, task_id)
    if task is None:
        raise ApiError(404, "not_found", f"task {task_id} not found")
    allowed = {"status", "title", "due_at", "tags"}
    try:
        patch = TaskPatch.model_validate(
            {k: v for k, v in changes.items() if k in allowed}
        )
    except ValidationError as exc:
        raise ApiError(
            400, "invalid_patch", str(exc.errors(include_url=False))
        ) from exc
    updates = patch.model_dump(exclude_unset=True)
    # Explicit nulls would corrupt required Task fields in Firestore and make
    # the doc unreadable; only due_at may be cleared.
    for field in ("status", "title", "tags"):
        if field in updates and updates[field] is None:
            raise ApiError(400, "invalid_patch", f"{field} cannot be null")
    if not updates:
        raise ApiError(400, "empty_update", "no updatable fields given")
    updates["updated_at"] = store.now()
    store.update(Task, task_id, updates)
    updated = store.get(Task, task_id)
    assert updated is not None
    return updated


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, body: dict) -> dict:
    return {"task": dump(_apply_task_changes(task_id, body))}


@router.get("/approvals")
def list_approvals(status: str = "pending") -> dict:
    approvals = store.query(Approval, filters=[("status", "==", status)], limit=200)
    return {"approvals": [dump(a) for a in approvals]}


def _get_pending_approval(approval_id: str) -> Approval:
    approval = store.get(Approval, approval_id)
    if approval is None:
        raise ApiError(404, "not_found", f"approval {approval_id} not found")
    if approval.status != "pending":
        raise ApiError(409, "already_resolved", f"approval is {approval.status}")
    return approval


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str) -> dict:
    approval = _get_pending_approval(approval_id)
    action = approval.action
    if isinstance(action, TaskUpdateAction):
        task = _apply_task_changes(action.task_id, action.changes)
        result = f"updated task {task.id}"
    elif isinstance(action, TaskCreateAction):
        spec = action.task
        title = str(spec.get("title", "")).strip()
        if not title:
            raise ApiError(400, "invalid_action", "task_create requires a title")
        try:
            task = Task(
                id=new_ulid(),
                title=title,
                created_at=store.now(),
                updated_at=store.now(),
                due_at=spec.get("due_at"),
                due_hint=spec.get("due_hint"),
                tags=spec.get("tags") or [],
            )
        except ValidationError as exc:
            raise ApiError(
                400, "invalid_action", str(exc.errors(include_url=False))
            ) from exc
        store.put(task)
        result = f"created task {task.id}"
    else:  # pragma: no cover — action union is closed
        raise ApiError(400, "invalid_action", "unknown action type")
    store.update(
        Approval,
        approval_id,
        {"status": "approved", "resolved_at": store.now(), "result": result},
    )
    refreshed = store.get(Approval, approval_id)
    assert refreshed is not None
    return {"approval": dump(refreshed)}


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str) -> dict:
    _get_pending_approval(approval_id)
    store.update(
        Approval, approval_id, {"status": "rejected", "resolved_at": store.now()}
    )
    refreshed = store.get(Approval, approval_id)
    assert refreshed is not None
    return {"approval": dump(refreshed)}


@router.get("/routines/runs")
def list_runs(limit: int = 20) -> dict:
    runs = store.query(RoutineRun, limit=limit)
    return {"runs": [dump(r, include_trace=False) for r in runs]}


@router.get("/routines/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = store.get(RoutineRun, run_id)
    if run is None:
        raise ApiError(404, "not_found", f"routine run {run_id} not found")
    return {"run": dump(run)}
