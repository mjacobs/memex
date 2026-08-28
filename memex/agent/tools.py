"""Agent function tools — the only writes the model can make (contracts.md).

Signatures match docs/contracts.md exactly; all go through
memex.store.firestore. `update_task` is ONLY wired into capture enrichment
paths — routine sessions get every tool EXCEPT it and must propose task
mutations via `queue_approval`.

A run-scoped context (`run_context`) lets routine sessions attribute
`create_note` / `queue_approval` writes to their RoutineRun and collect the
ids the run produced.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta

from pydantic import TypeAdapter, ValidationError

from memex.ids import new_ulid
from memex.models import Approval, ApprovalAction, Note, Task
from memex.store import firestore as store


@dataclass
class RunContext:
    """Collects what a routine session wrote, for the RoutineRun doc."""

    routine_run_id: str
    note_ids: list[str] = field(default_factory=list)
    approval_ids: list[str] = field(default_factory=list)


_run_context: ContextVar[RunContext | None] = ContextVar("memex_run_ctx", default=None)

_action_adapter: TypeAdapter[ApprovalAction] = TypeAdapter(ApprovalAction)


@contextmanager
def run_context(routine_run_id: str) -> Iterator[RunContext]:
    ctx = RunContext(routine_run_id=routine_run_id)
    token = _run_context.set(ctx)
    try:
        yield ctx
    finally:
        _run_context.reset(token)


def create_note(
    kind: str,
    body: str,
    summary: str,
    tags: list[str],
    transcript: str | None = None,
    capture_id: str | None = None,
    routine_run_id: str | None = None,
) -> dict:
    """Write a note to the feed. kind: "capture" | "digest" | "review"."""
    ctx = _run_context.get()
    if routine_run_id is None and ctx is not None:
        routine_run_id = ctx.routine_run_id
    note = Note(
        id=new_ulid(),
        created_at=store.now(),
        kind=kind,  # type: ignore[arg-type]  # pydantic validates the literal
        capture_id=capture_id,
        routine_run_id=routine_run_id,
        transcript=transcript,
        body=body,
        summary=summary,
        tags=tags,
    )
    store.put(note)
    if ctx is not None:
        ctx.note_ids.append(note.id)
    return {"note_id": note.id}


def create_tasks(tasks: list[dict], source_note_id: str) -> dict:
    """Create tasks extracted from a note.

    tasks: [{title, due_hint?, due_at?, tags?}] -> {task_ids: [...]}
    """
    task_ids: list[str] = []
    for spec in tasks:
        now = store.now()
        task = Task(
            id=new_ulid(),
            title=spec["title"],
            created_at=now,
            updated_at=now,
            due_hint=spec.get("due_hint"),
            due_at=spec.get("due_at"),
            tags=spec.get("tags") or [],
            source_note_id=source_note_id,
        )
        store.put(task)
        task_ids.append(task.id)
    if task_ids and source_note_id:
        note = store.get(Note, source_note_id)
        if note is not None:
            store.update(Note, source_note_id, {"task_ids": note.task_ids + task_ids})
    return {"task_ids": task_ids}


def list_tasks(status: str = "open", limit: int = 100) -> dict:
    """List tasks by status ("open" | "done" | "dropped")."""
    tasks = store.query(
        Task, filters=[("status", "==", status)], limit=limit, descending=False
    )
    return {"tasks": [t.model_dump(mode="json") for t in tasks]}


def update_task(task_id: str, changes: dict) -> dict:
    """Mutate a task directly. ONLY callable from capture enrichment;
    routines must use queue_approval."""
    allowed = {"status", "title", "due_at", "due_hint", "tags"}
    bad = set(changes) - allowed
    if bad:
        return {"error": f"disallowed fields: {sorted(bad)}"}
    if store.get(Task, task_id) is None:
        return {"error": f"task {task_id} not found"}
    store.update(Task, task_id, {**changes, "updated_at": store.now()})
    task = store.get(Task, task_id)
    assert task is not None
    return {"task": task.model_dump(mode="json")}


def list_recent_notes(limit: int = 50, days: int | None = None) -> dict:
    """List recent notes, newest first, optionally only the last N days."""
    filters: list[tuple[str, str, object]] = []
    order_by = "id"
    if days is not None:
        filters.append(("created_at", ">=", store.now() - timedelta(days=days)))
        order_by = "created_at"
    notes = store.query(Note, filters=filters, order_by=order_by, limit=limit)
    return {
        "notes": [n.model_dump(mode="json", exclude={"trace"}) for n in notes]
    }


def queue_approval(action: dict, reason: str) -> dict:
    """Queue an agent-proposed task mutation for human approval.

    action: {"type": "task_update", "task_id", "changes"} or
            {"type": "task_create", "task": {...}} per the Action contract.
    """
    try:
        validated = _action_adapter.validate_python(action)
    except ValidationError as exc:
        return {"error": f"invalid action: {exc.errors(include_url=False)}"}
    ctx = _run_context.get()
    approval = Approval(
        id=new_ulid(),
        created_at=store.now(),
        action=validated,
        reason=reason,
        routine_run_id=ctx.routine_run_id if ctx is not None else None,
    )
    store.put(approval)
    if ctx is not None:
        ctx.approval_ids.append(approval.id)
    return {"approval_id": approval.id}


# Toolset for routine sessions: no direct task writes (neither update_task
# nor create_tasks) — routines propose every task mutation through the
# approval queue (contracts.md task_update / task_create actions).
ROUTINE_TOOLS = [create_note, list_tasks, list_recent_notes, queue_approval]
