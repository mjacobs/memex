"""Shared entity models mirroring docs/contracts.md.

These are the contract types every workstream imports. Field changes go
through docs/contracts.md first.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CaptureSource = Literal["ios", "desktop", "web", "api"]
CaptureKind = Literal["text", "audio", "image", "link"]
CaptureStatus = Literal["pending", "processing", "enriched", "failed"]
NoteKind = Literal["capture", "digest", "review", "link"]
TaskStatus = Literal["open", "done", "dropped"]
ApprovalStatus = Literal["pending", "approved", "rejected"]
RoutineName = Literal["daily_review", "nightly_digest"]
RoutineStatus = Literal["running", "succeeded", "failed"]


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
    status: CaptureStatus
    processing_at: datetime | None = None  # set when an enrichment run claims it
    error: str | None = None
    note_id: str | None = None


class Note(BaseModel):
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
    trace: list[TraceEvent] = Field(default_factory=list)


class Task(BaseModel):
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


class EnrichmentResult(BaseModel):
    """Structured output of the single enrichment call (verified 2026-08-27)."""

    transcript: str
    summary: str
    tags: list[str]
    action_items: list["ActionItem"]


class ActionItem(BaseModel):
    title: str
