"""The agent seam W2 calls (contracts.md).

- enrich_capture(capture_id): run the single structured enrichment call for a
  text or audio capture, persist Note + Tasks, link the capture.
- run_routine(routine): run one ADK routine session, persist the RoutineRun.

Both are synchronous (run_routine drives the ADK session via asyncio.run) —
call them from FastAPI path functions declared with plain `def`, or via
`anyio.to_thread` from async code.
"""

import asyncio
import logging
from datetime import timedelta

from google.cloud import storage

from memex.agent import routines as routines_mod
from memex.agent import tools
from memex.agent.enrichment import enrich_audio, enrich_image, enrich_link, enrich_text
from memex.config import settings
from memex.ids import new_ulid
from memex.models import Capture, Note, RoutineRun, Task, TraceEvent
from memex.store import firestore as store

logger = logging.getLogger(__name__)


READ_LATER_TAG = "read-later"


_MD_ESCAPE = str.maketrans({c: f"\\{c}" for c in "\\`*_[]<>"})


def _md_text(text: str) -> str:
    """Prose dropped into a body the app composes — a screenshot description,
    the caption typed with it. It is content, not markup: a screenshot of code
    reading "List<T>" must survive to the page, where the sanitizer would
    otherwise drop it as an unknown tag. Line structure is left alone."""
    return text.translate(_MD_ESCAPE)


def _md_label(text: str) -> str:
    """Link text taken off a web page. It is data, not markup: a newline would
    end the paragraph and let the title add its own headings, brackets would
    close the link early, and a raw "<" could open an anchor of its own that
    the sanitizer has no reason to strip. All of it comes out literal."""
    flattened = "".join(c if c.isprintable() else " " for c in text)
    return flattened.translate(_MD_ESCAPE).strip()


def _md_url(url: str) -> str:
    """Link destination taken from a page URL. A ")" ends the destination
    early — leaving the rest of the URL as body text — and control characters
    would break the line, so both are percent-encoded away."""
    encoded = "".join(
        f"%{ord(c):02X}" if (c in "()<> " or not c.isprintable()) else c for c in url
    )
    return encoded


def _link_body(capture: Capture) -> str:
    """Markdown body for a link capture: the clickable link on line one.

    Built in code rather than asked of the model, so the note the SPA renders
    always leads with a working link to the saved page.
    """
    url = capture.url or ""
    label = _md_label(capture.title or "") or _md_label(url)
    body = f"[{label}]({_md_url(url)})"
    note = _md_text((capture.text or "").strip())
    return f"{body}\n\n{note}" if note else body


def _link_tags(tags: list[str]) -> list[str]:
    """Guarantee the read-later tag so saved links stay filterable."""
    return tags if READ_LATER_TAG in tags else [READ_LATER_TAG, *tags]


def _download_gcs(gcs_uri: str) -> bytes:
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"not a gs:// uri: {gcs_uri}")
    bucket_name, _, blob_name = gcs_uri.removeprefix("gs://").partition("/")
    client = storage.Client(project=settings().project)
    return client.bucket(bucket_name).blob(blob_name).download_as_bytes()


def _image_note_body(capture: Capture, description: str) -> str:
    """Markdown body for a screenshot note: what's in it, then provenance."""
    parts = [_md_text(description)]
    if capture.text:
        parts.append(f"**Note:** {_md_text(capture.text)}")
    if capture.source_url:
        label = _md_label(capture.title or "") or _md_label(capture.source_url)
        parts.append(f"Source: [{label}]({_md_url(capture.source_url)})")
    elif capture.title:
        parts.append(f"Source: {_md_label(capture.title)}")
    return "\n\n".join(parts)


def enrich_capture(capture_id: str) -> dict:
    """Enrich one capture end to end. Never raises for enrichment failures —
    the capture is marked failed and the error is returned in the dict."""
    capture = store.get(Capture, capture_id)
    if capture is None:
        return {"capture": None, "note": None, "tasks": [], "error": f"capture {capture_id} not found"}

    # Eventarc delivery is at-least-once: a redelivered finalize event must
    # not re-enrich (duplicate notes/tasks). "processing" younger than 30
    # minutes is an in-flight run; older is treated as crashed and retried.
    if capture.status == "enriched" and capture.note_id:
        note = store.get(Note, capture.note_id)
        tasks = (
            [t for tid in note.task_ids if (t := store.get(Task, tid))] if note else []
        )
        return {
            "capture": capture.model_dump(mode="json"),
            "note": note.model_dump(mode="json") if note else None,
            "tasks": [t.model_dump(mode="json") for t in tasks],
            "deduped": True,
        }
    started = capture.processing_at or capture.created_at
    if capture.status == "processing" and store.now() - started < timedelta(minutes=30):
        return {
            "capture": capture.model_dump(mode="json"),
            "note": None,
            "tasks": [],
            "in_progress": True,
        }

    store.update(
        Capture, capture_id, {"status": "processing", "processing_at": store.now()}
    )
    capture.status = "processing"
    try:
        trace: list[TraceEvent] = []
        if capture.kind == "audio":
            if not capture.audio_gcs_uri:
                raise ValueError("audio capture has no audio_gcs_uri")
            mime = capture.audio_mime or "audio/mp4"
            audio = _download_gcs(capture.audio_gcs_uri)
            trace.append(
                TraceEvent(
                    t=store.now(),
                    role="user",
                    text=f"[audio capture {capture.audio_gcs_uri} ({mime}, {len(audio)} bytes)]",
                )
            )
            result = enrich_audio(audio, mime)
        elif capture.kind == "image":
            if not capture.image_gcs_uri:
                raise ValueError("image capture has no image_gcs_uri")
            mime = capture.image_mime or "image/png"
            image = _download_gcs(capture.image_gcs_uri)
            trace.append(
                TraceEvent(
                    t=store.now(),
                    role="user",
                    text=(
                        f"[image capture {capture.image_gcs_uri} "
                        f"({mime}, {len(image)} bytes)]"
                        + (f" caption: {capture.text}" if capture.text else "")
                        + (f" from: {capture.source_url}" if capture.source_url else "")
                    ),
                )
            )
            result = enrich_image(
                image,
                mime,
                caption=capture.text,
                source_url=capture.source_url,
                title=capture.title,
            )
        elif capture.kind == "link":
            if not capture.url:
                raise ValueError("link capture has no url")
            trace.append(
                TraceEvent(
                    t=store.now(),
                    role="user",
                    text=f"[link capture {capture.url} ({capture.title or 'untitled'})]",
                )
            )
            result = enrich_link(capture.url, capture.title, capture.text)
        else:
            if capture.text is None:
                raise ValueError("text capture has no text")
            trace.append(TraceEvent(t=store.now(), role="user", text=capture.text))
            result = enrich_text(capture.text)

        trace.append(
            TraceEvent(
                t=store.now(),
                role="model",
                text=result.summary,
                tool="enrich",
                result=result.model_dump(mode="json"),
            )
        )

        if capture.kind == "link":
            body = _link_body(capture)
        elif capture.kind == "text":
            body = capture.text
        elif capture.kind == "image":
            body = _image_note_body(capture, result.transcript)
        else:
            body = result.transcript
        note = Note(
            id=new_ulid(),
            created_at=store.now(),
            kind="link" if capture.kind == "link" else "capture",
            capture_id=capture.id,
            transcript=result.transcript if capture.kind == "audio" else None,
            body=body,
            summary=result.summary,
            tags=_link_tags(result.tags) if capture.kind == "link" else result.tags,
            trace=trace,
        )
        store.put(note)

        task_ids: list[str] = []
        if result.action_items:
            created = tools.create_tasks(
                [item.model_dump(mode="json", exclude_none=True) for item in result.action_items],
                source_note_id=note.id,
            )
            task_ids = created["task_ids"]
            note.task_ids = task_ids

        store.update(
            Capture, capture.id, {"status": "enriched", "note_id": note.id, "error": None}
        )
        capture.status = "enriched"
        capture.note_id = note.id
        tasks_out = [t for tid in task_ids if (t := store.get(Task, tid)) is not None]
        return {
            "capture": capture.model_dump(mode="json"),
            "note": note.model_dump(mode="json"),
            "tasks": [t.model_dump(mode="json") for t in tasks_out],
        }
    except Exception as exc:
        logger.exception("enrichment failed for capture %s", capture_id)
        store.update(Capture, capture_id, {"status": "failed", "error": str(exc)})
        capture.status = "failed"
        capture.error = str(exc)
        return {
            "capture": capture.model_dump(mode="json"),
            "note": None,
            "tasks": [],
            "error": str(exc),
        }


def run_routine(routine: str) -> dict:
    """Run one routine ("daily_review" | "nightly_digest") as an agent session;
    persist and return the RoutineRun as a JSON-able dict."""
    run = RoutineRun(id=new_ulid(), routine=routine, fired_at=store.now())  # type: ignore[arg-type]
    store.put(run)
    try:
        with tools.run_context(run.id) as ctx:
            result = asyncio.run(routines_mod.run_routine_session(routine))
        run.summary = result.summary
        run.trace = result.trace
        run.note_id = ctx.note_ids[-1] if ctx.note_ids else None
        run.approval_ids = ctx.approval_ids
        if run.note_id is None:
            # Both routines must end in create_note; a run without one is
            # incomplete and should be retried by the scheduler.
            run.status = "failed"
            run.error = "routine session produced no note"
        else:
            run.status = "succeeded"
    except Exception as exc:
        logger.exception("routine %s failed (run %s)", routine, run.id)
        run.status = "failed"
        run.error = str(exc)
    store.put(run)
    return run.model_dump(mode="json")
