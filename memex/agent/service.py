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
import re
from datetime import timedelta

from google.cloud import storage

from memex.agent import routines as routines_mod
from memex.agent import tools
from memex.agent.enrichment import (
    enrich_audio,
    enrich_image,
    enrich_link,
    enrich_text,
    start_requested_research,
)
from memex.config import settings
from memex.ids import new_ulid
from memex.models import Capture, Note, RoutineRun, Task, TraceEvent
from memex.store import firestore as store

logger = logging.getLogger(__name__)


READ_LATER_TAG = "read-later"


_MD_ESCAPE = str.maketrans({c: f"\\{c}" for c in "\\`*_[]<>"})


# A line opening with one of these is a heading, quote, list, or rule, and
# transcribed text opens lines that way all the time — "- buy milk", "1. run
# the migration", "# TODO".
_MD_BLOCK_START = re.compile(r"^([ \t]*)([#>+=~-]|\d+[.)])", re.MULTILINE)


def _escape_block_start(m: re.Match[str]) -> str:
    indent, marker = m.group(1), m.group(2)
    if marker[0].isdigit():  # "1." escapes on the dot, not the digit
        return f"{indent}{marker[:-1]}\\{marker[-1]}"
    return f"{indent}\\{marker}"


def _md_text(text: str) -> str:
    """Prose dropped into a body the app composes — a screenshot description,
    the caption typed with it. It is content, not markup: a screenshot of code
    reading "List<T>" must survive to the page, where the sanitizer would
    otherwise drop it as an unknown tag, and a transcribed "# TODO" must stay
    a line of text rather than becoming a heading."""
    return _MD_BLOCK_START.sub(_escape_block_start, text.translate(_MD_ESCAPE))


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
        return {
            "capture": None,
            "note": None,
            "tasks": [],
            "error": f"capture {capture_id} not found",
        }

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
                    # The user's note is what a task off this link may be
                    # derived from, so it has to be in the trace to audit.
                    args={
                        "url": capture.url,
                        "title": capture.title,
                        "note": capture.text,
                    },
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

        action_items = result.action_items
        if capture.kind == "link" and not (capture.text or "").strip():
            # A saved link is a URL and a title the site chose. Nothing there
            # is the user asking for anything, so a task out of a bare link
            # can only have come from the page talking to the model. The link
            # prompt already says page text is not instructions; enforcing it
            # here makes it true.
            action_items = []

        task_ids: list[str] = []
        if action_items:
            created = tools.create_tasks(
                [item.model_dump(mode="json", exclude_none=True) for item in action_items],
                source_note_id=note.id,
            )
            task_ids = created["task_ids"]
            note.task_ids = task_ids

        store.update(
            Capture, capture.id, {"status": "enriched", "note_id": note.id, "error": None}
        )
        capture.status = "enriched"
        capture.note_id = note.id
        # After the capture note lands: the client's explicit research flag —
        # and nothing else — starts a deep-research operation. A run spends
        # real money and ships the note to an external service, so the model's
        # reading of page text never authorizes one (contracts.md). Never
        # fails the capture, but the outcome rides back in the result so a
        # kickoff that failed is visible to the caller and not only to the log.
        # The report lands as its own note whatever the capture was, so the
        # capture note here is finished either way and the run only adds to it.
        research = start_requested_research(note) if capture.research else None
        if research is not None and not research.get("error"):
            # The kickoff stamped research_status onto the stored note; re-read
            # so the response says a report is coming rather than describing
            # the note as it was a moment before.
            #
            # Best-effort, and deliberately outside the enclosing try: by now
            # the note is written, the capture is enriched, and a paid run is
            # going. Letting a flaky read fail the capture would mark all of
            # that failed, and the composer hands the text back for a retry
            # that writes a second note and buys a second run. The kickoff's
            # outcome is already in `research`; a stale badge is not worth it.
            try:
                note = store.get(Note, note.id) or note
            except Exception:
                logger.exception(
                    "could not re-read note %s after its research kickoff; "
                    "returning it as written",
                    note.id,
                )
        tasks_out = [t for tid in task_ids if (t := store.get(Task, tid)) is not None]
        result_out = {
            "capture": capture.model_dump(mode="json"),
            "note": note.model_dump(mode="json"),
            "tasks": [t.model_dump(mode="json") for t in tasks_out],
        }
        if research is not None:
            result_out["research"] = research
        return result_out
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
