"""/api/v1 routes per docs/contracts.md. All Firestore access via the store."""

import base64
import binascii
import logging
from typing import Any, get_args
from urllib.parse import urlparse

import anyio.to_thread
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, ValidationError

from memex.api import gcs
from memex.api.auth import require_device
from memex.api.common import ApiError, dump
from memex.ids import new_ulid
from memex.models import (
    Approval,
    Capture,
    CaptureSource,
    Note,
    OperationStatus,
    RoutineRun,
    Task,
    TaskCreateAction,
    TaskStatus,
    TaskUpdateAction,
    TraceEvent,
    clean_tags,
)
from memex.store import firestore as store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_device)])

AUDIO_EXTENSIONS = {
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "audio/webm": "webm",
}

IMAGE_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

MAX_IMAGE_BYTES = 10 * 1024 * 1024


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


def _enrich_or_fail(capture: Capture) -> dict:
    """Run enrichment for a just-written capture; mark the capture failed and
    raise ApiError on error. enrich_capture reports enrichment failures in its
    result rather than raising, so both shapes are handled here."""
    try:
        result = _enrich(capture.id)
    except ApiError:
        raise
    except Exception as exc:
        store.update(Capture, capture.id, {"status": "failed", "error": str(exc)})
        raise ApiError(502, "enrichment_failed", str(exc)) from exc
    if result.get("error"):
        raise ApiError(502, "enrichment_failed", result["error"])
    return result


def _capture_result(capture: Capture, enrichment: dict | None = None) -> dict:
    """The {capture, note, tasks} envelope the sync capture paths return.

    A capture that asked for research also carries `research`:
    `{"operation_id": ...}` when the run started, `{"error": ...}` when it
    could not — a failed kickoff would otherwise be indistinguishable from a
    capture that never asked for research.
    """
    capture = store.get(Capture, capture.id) or capture
    note = store.get(Note, capture.note_id) if capture.note_id else None
    tasks: list[Task] = []
    if note:
        tasks = [t for tid in note.task_ids if (t := store.get(Task, tid))]
    out = {
        "capture": dump(capture),
        "note": dump(note) if note else None,
        "tasks": [dump(t) for t in tasks],
    }
    research = (enrichment or {}).get("research")
    if research is not None:
        out["research"] = research
    return out


class CaptureIn(BaseModel):
    text: str
    source: str | None = None
    # Explicit "research this" from the client's own affordance. Optional, so
    # a client that never sends it simply never starts a paid run.
    research: bool = False


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
        research=body.research,
        status="pending",
    )
    store.put(capture)
    return _capture_result(capture, _enrich_or_fail(capture))


class LinkIn(BaseModel):
    url: str
    title: str | None = None
    note: str | None = None
    research: bool = False


class LinksIn(BaseModel):
    # Untyped entries, validated one at a time in the handler: any shape
    # Pydantic could reject here — a wrong type, a null, a missing url —
    # would be a 422 for the whole batch, and this endpoint promises that
    # each link succeeds or fails on its own.
    links: list[Any]
    source: str | None = None


MAX_URL_LENGTH = 2048
MAX_LINKS_PER_BATCH = 20


def _http_url(url: str | None) -> str | None:
    """The URL if it is a usable http(s) page address, else None.

    urlparse raises on some malformed input ("https://[::1"), which would be
    a 500 on a route whose whole job is validating client-supplied text.
    """
    if not url:
        return None
    url = url.strip()
    if not url or len(url) > MAX_URL_LENGTH:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    return url


def _clean_url(url: str) -> str:
    """Validate a client-supplied URL. http/https only — anything else (file:,
    javascript:, chrome-extension:) is not a page anyone can read later, and
    the URL is echoed into a rendered markdown link."""
    cleaned = _http_url(url)
    if cleaned is None:
        raise ApiError(
            400,
            "invalid_url",
            f"url must be an http(s) URL under {MAX_URL_LENGTH} characters",
        )
    return cleaned


def _optional_url(url: str | None) -> str | None:
    """A provenance URL we can offer as a link, or nothing.

    Unlike a saved link — where a bad URL is the whole request and earns a
    400 — a screenshot's source page is a nice-to-have. A chrome-extension:
    or file: URL just gets dropped rather than costing the user the capture.
    """
    return _http_url(url)


def _flag(value: str | None) -> bool:
    """A boolean request header. Audio arrives as a raw body, so its flags
    ride headers rather than JSON fields."""
    return (value or "").strip().lower() in ("1", "true", "yes")


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value[:limit] if value else None


def _save_link(link: LinkIn, source: str | None, device_id: str) -> Capture:
    """Persist one link capture (pending) after validating its URL."""
    capture = Capture(
        id=new_ulid(),
        created_at=store.now(),
        source=_source(source),
        device_id=device_id,
        kind="link",
        url=_clean_url(link.url),
        title=_truncate(link.title, 500),
        text=_truncate(link.note, 4000),
        research=link.research,
        status="pending",
    )
    store.put(capture)
    return capture


@router.post("/capture/link", status_code=201)
def capture_link(body: LinkIn, device_id: str = Depends(require_device)) -> dict:
    """Save one link as a read-later note. The page is never fetched server
    side; the note is written from the URL, title, and user note alone."""
    capture = _save_link(body, None, device_id)
    return _capture_result(capture, _enrich_or_fail(capture))


@router.post("/capture/links", status_code=201)
def capture_links(body: LinksIn, device_id: str = Depends(require_device)) -> dict:
    """Batch form of /capture/link — one triage session saves several tabs at
    once, and a per-link round trip would mean N auth + N requests.

    Each link succeeds or fails on its own: a bad URL or a failed enrichment is
    reported in that link's result rather than failing the whole batch, so a
    client never has to guess which of its links landed.
    """
    if not body.links:
        raise ApiError(400, "empty_batch", "links must be non-empty")
    if len(body.links) > MAX_LINKS_PER_BATCH:
        raise ApiError(
            400,
            "batch_too_large",
            f"at most {MAX_LINKS_PER_BATCH} links per request",
        )
    results: list[dict] = []
    for raw in body.links:
        url = raw.get("url") if isinstance(raw, dict) else None
        try:
            link = LinkIn.model_validate(raw)
        except ValidationError as exc:
            results.append(
                {
                    "url": url if isinstance(url, str) else None,
                    "error": {
                        "code": "invalid_link",
                        "message": str(exc.errors(include_url=False)),
                    },
                }
            )
            continue
        try:
            capture = _save_link(link, body.source, device_id)
            enrichment = _enrich_or_fail(capture)
        except ApiError as exc:
            results.append(
                {"url": link.url, "error": {"code": exc.code, "message": exc.message}}
            )
            continue
        results.append({"url": link.url, **_capture_result(capture, enrichment)})
    return {"results": results}


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
        research=_flag(request.headers.get("x-memex-research")),
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


class ImageCaptureIn(BaseModel):
    """Screenshot push (Snippy). JSON rather than a raw body like audio: an
    image capture carries metadata (caption, source page) that has to survive
    non-ASCII, which HTTP headers can't carry safely."""

    image_base64: str
    mime: str
    text: str | None = None  # caption / note typed alongside the screenshot
    source_url: str | None = None
    title: str | None = None
    source: str | None = None
    research: bool = False


@router.post("/capture/image", status_code=202)
async def capture_image(
    body: ImageCaptureIn, device_id: str = Depends(require_device)
) -> dict:
    mime = body.mime.split(";")[0].strip().lower()
    ext = IMAGE_EXTENSIONS.get(mime)
    if ext is None:
        raise ApiError(
            415,
            "unsupported_media_type",
            f"unsupported image content-type: {mime or '(none)'}",
        )
    # Cheap length gate before decoding, so an oversized payload can't cost a
    # full base64 decode. 4 base64 chars per 3 bytes, plus padding slack.
    if len(body.image_base64) > (MAX_IMAGE_BYTES // 3 + 1) * 4 + 4:
        raise ApiError(413, "payload_too_large", "image exceeds 10 MiB")
    try:
        data = base64.b64decode(body.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ApiError(
            400, "invalid_base64", "image_base64 is not valid base64"
        ) from exc
    if not data:
        raise ApiError(400, "empty_body", "image must be non-empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ApiError(413, "payload_too_large", "image exceeds 10 MiB")

    capture_id = new_ulid()
    # Capture doc first: the GCS finalize event races Eventarc against this
    # write, and /internal/enrich 404s if the doc isn't there yet.
    capture = Capture(
        id=capture_id,
        created_at=store.now(),
        source=_source(body.source),
        device_id=device_id,
        kind="image",
        text=_truncate(body.text, 4000),
        image_gcs_uri=gcs.image_uri(capture_id, ext),
        image_mime=mime,
        source_url=_optional_url(body.source_url),
        title=_truncate(body.title, 500),
        research=body.research,
        status="pending",
    )
    await anyio.to_thread.run_sync(store.put, capture)
    try:
        await anyio.to_thread.run_sync(gcs.upload_image, capture_id, ext, data, mime)
    except Exception as exc:
        store.update(Capture, capture_id, {"status": "failed", "error": str(exc)})
        raise ApiError(502, "upload_failed", "image upload failed") from exc
    return {"id": capture_id}


@router.get("/captures/{capture_id}")
def get_capture(capture_id: str) -> dict:
    capture = store.get(Capture, capture_id)
    if capture is None:
        raise ApiError(404, "not_found", f"capture {capture_id} not found")
    return {"capture": dump(capture)}


@router.get("/captures/{capture_id}/image")
async def get_capture_image(capture_id: str) -> Response:
    """Serve the stored screenshot bytes.

    Proxied rather than handed out as a signed URL: signing from Cloud Run
    means an IAM SignBlob round trip per view (the runtime service account has
    no private key), and the bucket stays private either way.
    """
    capture = store.get(Capture, capture_id)
    if capture is None:
        raise ApiError(404, "not_found", f"capture {capture_id} not found")
    if capture.kind != "image" or not capture.image_gcs_uri:
        raise ApiError(404, "not_found", f"capture {capture_id} has no image")
    try:
        data = await anyio.to_thread.run_sync(gcs.download, capture.image_gcs_uri)
    except Exception as exc:
        raise ApiError(502, "download_failed", "image download failed") from exc
    return Response(
        content=data,
        media_type=capture.image_mime or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=3600"},
    )


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
    body: dict = {"note": dump(note)}
    if note.capture_id:
        capture = store.get(Capture, note.capture_id)
        if capture and capture.kind == "image" and capture.image_gcs_uri:
            # Same-origin, bearer-authenticated: the SPA fetches it and turns
            # it into a blob URL (an <img src> can't carry the device key).
            body["image_url"] = f"/api/v1/captures/{capture.id}/image"
    return body


class NotePatch(BaseModel):
    """Owner edits to a note. Unknown fields are a 422 from FastAPI."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None = None
    body: str | None = None
    tags: list[str] | None = None


def _edit_summary(fields: list[str]) -> str:
    """ "summary", "tags" -> "summary and tags"; three -> "a, b and c"."""
    if len(fields) == 1:
        return fields[0]
    return f"{', '.join(fields[:-1])} and {fields[-1]}"


@router.patch("/notes/{note_id}")
def patch_note(note_id: str, body: NotePatch) -> dict:
    note = store.get(Note, note_id)
    if note is None:
        raise ApiError(404, "not_found", f"note {note_id} not found")
    updates = body.model_dump(exclude_unset=True)
    # Explicit nulls would corrupt required Note fields in Firestore and make
    # the doc unreadable; there is no nullable field to clear here.
    for field, value in updates.items():
        if value is None:
            raise ApiError(400, "invalid_patch", f"{field} cannot be null")
    if "tags" in updates:
        updates["tags"] = clean_tags(updates["tags"])
    changed = [f for f in ("summary", "body", "tags") if f in updates]
    if not changed:
        raise ApiError(400, "empty_update", "no updatable fields given")
    # The trace is the honesty surface: an owner edit is recorded as a user
    # event alongside the model's own work, not applied silently.
    args: dict = {"fields": changed}
    if "tags" in updates:
        args["tags"] = {"before": list(note.tags), "after": list(updates["tags"])}
    event = TraceEvent(
        t=store.now(),
        role="user",
        text=f"Edited {_edit_summary(changed)}",
        args=args,
    )
    # Appended server-side rather than rewritten from the copy we just read:
    # two edits landing at once must not drop one another's audit event.
    store.update(
        Note,
        note_id,
        {**updates, "trace": store.array_union([event.model_dump(mode="python")])},
    )
    updated = store.get(Note, note_id)
    assert updated is not None
    return {"note": dump(updated)}


@router.post("/notes/{note_id}/research", status_code=202)
def start_note_research(note_id: str) -> dict:
    """Research a note that already exists.

    The other half of the capture's `research` flag: same rule, same reason —
    a run spends real money and ships the note to an external service, so it
    starts because the owner asked, never because of anything read out of a
    page (contracts.md).

    This path never merges. The note stands on its own already, so the report
    lands as its own `research` note pointing back here.
    """
    note = store.get(Note, note_id)
    if note is None:
        raise ApiError(404, "not_found", f"note {note_id} not found")
    if note.research_status == "running":
        raise ApiError(
            409, "already_running", f"note {note_id} already has a research run"
        )
    from memex.agent.research import start_research_operation

    result = start_research_operation(note_id)
    if result.get("error"):
        raise ApiError(502, "research_failed", result["error"])
    return {"operation_id": result["operation_id"], "status": "running"}


@router.delete("/notes/{note_id}")
def delete_note(note_id: str) -> dict:
    """Hard-delete the note and its originating capture, blob included — for
    an image note the screenshot is the content, so "delete" reclaims the
    bytes (contracts.md). Tasks spawned from the note survive with a dangling
    id — deleting a note is not a retraction of the work it produced."""
    note = store.get(Note, note_id)
    if note is None:
        raise ApiError(404, "not_found", f"note {note_id} not found")
    if note.capture_id and (capture := store.get(Capture, note.capture_id)):
        for uri in (capture.image_gcs_uri, capture.audio_gcs_uri):
            if uri:
                try:
                    gcs.delete(uri)
                except Exception:
                    # Reclaiming bytes is best-effort; the note delete the
                    # user asked for must not fail on a storage hiccup.
                    logger.warning("blob delete failed for %s", uri, exc_info=True)
        store.delete(Capture, note.capture_id)
    store.delete(Note, note_id)
    return {"deleted": note_id}


@router.get("/tasks")
def list_tasks(status: str = "open") -> dict:
    if status not in get_args(TaskStatus):
        raise ApiError(400, "invalid_status", f"invalid task status: {status}")
    tasks = store.query(Task, filters=[("status", "==", status)], limit=200)
    return {"tasks": [dump(t) for t in tasks]}


class TaskPatch(BaseModel):
    status: TaskStatus | None = None
    title: str | None = None
    tags: list[str] | None = None


def _apply_task_changes(task_id: str, changes: dict) -> Task:
    task = store.get(Task, task_id)
    if task is None:
        raise ApiError(404, "not_found", f"task {task_id} not found")
    allowed = {"status", "title", "tags"}
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
    # the doc unreadable.
    for field in ("status", "title", "tags"):
        if field in updates and updates[field] is None:
            raise ApiError(400, "invalid_patch", f"{field} cannot be null")
    if not updates:
        raise ApiError(400, "empty_update", "no updatable fields given")
    if "tags" in updates:
        updates["tags"] = clean_tags(updates["tags"])
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


@router.get("/operations")
def list_operations(status: str | None = None, limit: int = 50) -> dict:
    """List LRO queue entries (the feed's "research pending" badge polls
    ?status=running)."""
    if status is not None and status not in get_args(OperationStatus):
        raise ApiError(400, "invalid_status", f"invalid operation status: {status}")
    operations = store.list_operations(status=status, limit=limit)
    return {"operations": [dump(o) for o in operations]}


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
