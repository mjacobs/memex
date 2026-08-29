"""Shared entity models mirroring docs/contracts.md.

These are the contract types every workstream imports. Field changes go
through docs/contracts.md first.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CaptureSource = Literal["ios", "desktop", "web", "api"]
CaptureKind = Literal["text", "audio", "image", "link"]
CaptureStatus = Literal["pending", "processing", "enriched", "failed"]
NoteKind = Literal["capture", "digest", "review", "link", "research"]
TaskStatus = Literal["open", "done", "dropped"]
ApprovalStatus = Literal["pending", "approved", "rejected"]
RoutineName = Literal["daily_review", "nightly_digest"]
RoutineStatus = Literal["running", "succeeded", "failed"]
OperationKind = Literal["deep_research"]
OperationStatus = Literal["running", "completed", "failed"]


_TAG_SPLIT = re.compile(r",+")
_TAG_STRIP = re.compile(r"[^a-z0-9-]+")


def clean_tags(tags: list[str]) -> list[str]:
    """Normalize to the lowercase-kebab tags this contract promises.

    A tag doubles as a filter URL segment, and the reader splits that on
    commas — so a tag stored as "foo,bar" could never match the note carrying
    it. Rather than reject it, it becomes the two tags it plainly means;
    everything else outside lowercase kebab becomes a hyphen, so "Read Later"
    is the "read-later" it was going to be anyway. Duplicates and empties go.

    Enforced by the models rather than at each call site, because tags arrive
    from the user, from enrichment, and from routines, and only one of those
    can be asked to read the contract.
    """
    out: list[str] = []
    for raw in tags:
        for piece in _TAG_SPLIT.split(str(raw).strip().lower()):
            tag = _TAG_STRIP.sub("-", piece).strip("-")
            if tag and tag not in out:
                out.append(tag)
    return out


class _Tagged(BaseModel):
    """Mixin for the entities that carry user-facing tags."""

    @field_validator("tags", mode="after", check_fields=False)
    @classmethod
    def _normalize_tags(cls, tags: list[str]) -> list[str]:
        return clean_tags(tags)


class TraceEvent(BaseModel):
    t: datetime
    role: Literal["user", "model", "tool"]
    text: str | None = None
    tool: str | None = None
    args: dict | None = None
    result: dict | None = None


class Capture(BaseModel):
    id: str
    created_at: datetime
    source: CaptureSource = "api"
    device_id: str
    kind: CaptureKind
    # kind=text: the captured text. kind=link: the optional user note, if any.
    text: str | None = None
    # kind=link only: the saved page URL — never fetched server-side
    # (see docs/contracts.md).
    url: str | None = None
    # kind=image/link: page title as the client reported it.
    title: str | None = None
    audio_gcs_uri: str | None = None
    audio_mime: str | None = None
    image_gcs_uri: str | None = None
    image_mime: str | None = None
    # Provenance for kind=image: the page the screenshot was taken from.
    source_url: str | None = None
    # The user asked for a background research run on this capture. Set by
    # the client from an explicit affordance, never inferred from content —
    # a run spends real money and ships the note to an external service, and
    # a page the user only saved gets a say in neither.
    research: bool = False
    status: CaptureStatus
    processing_at: datetime | None = None  # set when an enrichment run claims it
    error: str | None = None
    note_id: str | None = None


class Note(_Tagged):
    id: str
    created_at: datetime
    kind: NoteKind
    capture_id: str | None = None
    routine_run_id: str | None = None
    transcript: str | None = None
    body: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    # kind=research: the note that asked for the research.
    source_note_id: str | None = None
    trace: list[TraceEvent] = Field(default_factory=list)


class Task(_Tagged):
    id: str
    title: str
    status: TaskStatus = "open"
    created_at: datetime
    updated_at: datetime
    tags: list[str] = Field(default_factory=list)
    source_note_id: str | None = None


class TaskUpdateAction(BaseModel):
    type: Literal["task_update"]
    task_id: str
    changes: dict


class TaskCreateAction(BaseModel):
    type: Literal["task_create"]
    task: dict


ApprovalAction = TaskUpdateAction | TaskCreateAction


class Approval(BaseModel):
    id: str
    created_at: datetime
    status: ApprovalStatus = "pending"
    action: TaskUpdateAction | TaskCreateAction = Field(discriminator="type")
    reason: str
    routine_run_id: str | None = None
    resolved_at: datetime | None = None
    result: str | None = None


class RoutineRun(BaseModel):
    id: str
    routine: RoutineName
    fired_at: datetime
    status: RoutineStatus = "running"
    summary: str | None = None
    note_id: str | None = None
    approval_ids: list[str] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    error: str | None = None


class Operation(BaseModel):
    """Durable long-running operation (Firestore queue + Cloud Tasks poll)."""

    id: str
    kind: OperationKind
    status: OperationStatus = "running"
    created_at: datetime
    updated_at: datetime
    # The aiplatform interaction handle (kind=deep_research).
    interaction_id: str
    source_note_id: str
    result_note_id: str | None = None
    attempts: int = 0
    error: str | None = None


class ChatSession(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    # First user message, truncated.
    title: str | None = None
    trace: list[TraceEvent] = Field(default_factory=list)


class EnrichmentResult(BaseModel):
    """Structured output of the single enrichment call (verified 2026-08-27)."""

    transcript: str
    summary: str
    tags: list[str]
    action_items: list["ActionItem"]


class ActionItem(BaseModel):
    title: str
