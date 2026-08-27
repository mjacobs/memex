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

from google.cloud import storage

from memex.agent import routines as routines_mod
from memex.agent import tools
from memex.agent.enrichment import enrich_audio, enrich_text
from memex.config import settings
from memex.ids import new_ulid
from memex.models import Capture, Note, RoutineRun, Task, TraceEvent
from memex.store import firestore as store

logger = logging.getLogger(__name__)


def _download_gcs(gcs_uri: str) -> bytes:
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"not a gs:// uri: {gcs_uri}")
    bucket_name, _, blob_name = gcs_uri.removeprefix("gs://").partition("/")
    client = storage.Client(project=settings().project)
    return client.bucket(bucket_name).blob(blob_name).download_as_bytes()


def enrich_capture(capture_id: str) -> dict:
    """Enrich one capture end to end. Never raises for enrichment failures —
    the capture is marked failed and the error is returned in the dict."""
    capture = store.get(Capture, capture_id)
    if capture is None:
        return {"capture": None, "note": None, "tasks": [], "error": f"capture {capture_id} not found"}

    store.update(Capture, capture_id, {"status": "processing"})
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

        note = Note(
            id=new_ulid(),
            created_at=store.now(),
            kind="capture",
            capture_id=capture.id,
            transcript=result.transcript if capture.kind == "audio" else None,
            body=capture.text if capture.kind == "text" else result.transcript,
            summary=result.summary,
            tags=result.tags,
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
        run.status = "succeeded"
        run.summary = result.summary
        run.trace = result.trace
        run.note_id = ctx.note_ids[-1] if ctx.note_ids else None
        run.approval_ids = ctx.approval_ids
    except Exception as exc:
        logger.exception("routine %s failed (run %s)", routine, run.id)
        run.status = "failed"
        run.error = str(exc)
    store.put(run)
    return run.model_dump(mode="json")
