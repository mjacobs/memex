"""Agent function tools — the only writes the model can make (contracts.md).

Signatures match docs/contracts.md exactly; all go through
memex.store.firestore. `update_task` is wired into capture enrichment paths
and chat sessions only — routine sessions get every tool EXCEPT the direct
mutators and must propose task mutations via `queue_approval`. Chat mutates
directly (the user's live instruction is the approval; contracts.md).

A run-scoped context (`run_context`) lets routine sessions attribute
`create_note` / `queue_approval` writes to their RoutineRun and collect the
ids the run produced.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta

from pydantic import (
    BaseModel,
    ConfigDict,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from memex.ids import new_ulid
from memex.models import (
    Approval,
    ApprovalAction,
    Note,
    Task,
    TaskStatus,
    TraceEvent,
    clean_tags,
)
from memex.store import firestore as store


@dataclass
class RunContext:
    """Collects what a routine session wrote, for the RoutineRun doc."""

    routine_run_id: str
    note_ids: list[str] = field(default_factory=list)
    approval_ids: list[str] = field(default_factory=list)


_run_context: ContextVar[RunContext | None] = ContextVar("memex_run_ctx", default=None)

_action_adapter: TypeAdapter[ApprovalAction] = TypeAdapter(ApprovalAction)


class _Patch(BaseModel):
    """Base for the direct-mutation patches (`update_task`, `update_note`).

    The model hands these tools an untyped dict, so the values are validated
    before anything is written — otherwise a plausible-looking
    `{"status": "completed"}` or a non-string body persists and only fails
    later, on read-back. Unknown fields are rejected, nulls mean "leave
    alone", and tags are normalized exactly as every other write normalizes
    them.
    """

    model_config = ConfigDict(extra="forbid")

    @field_validator("tags", mode="after", check_fields=False)
    @classmethod
    def _normalize_tags(cls, tags: list[str] | None) -> list[str] | None:
        return None if tags is None else clean_tags(tags)

    def updates(self) -> dict:
        """The fields actually being changed — nulls dropped."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class _TaskPatch(_Patch):
    status: TaskStatus | None = None
    title: str | None = None
    tags: list[str] | None = None


class _NotePatch(_Patch):
    summary: str | None = None
    body: str | None = None
    tags: list[str] | None = None


def _validated(patch: type[_Patch], changes: dict) -> tuple[dict | None, dict | None]:
    """(updates, error) — the patch's non-null fields, or a tool-shaped error."""
    try:
        parsed = patch.model_validate(changes)
    except ValidationError as exc:
        return None, {"error": f"invalid changes: {exc.errors(include_url=False)}"}
    updates = parsed.updates()
    if not updates:
        return None, {"error": "no updatable fields given"}
    return updates, None


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

    tasks: [{title, tags?}] -> {task_ids: [...]}
    """
    task_ids: list[str] = []
    for spec in tasks:
        now = store.now()
        task = Task(
            id=new_ulid(),
            title=spec["title"],
            created_at=now,
            updated_at=now,
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
    """Mutate a task directly. ONLY callable from capture enrichment and
    chat; routines must use queue_approval."""
    updates, error = _validated(_TaskPatch, changes)
    if error is not None:
        return error
    assert updates is not None
    if store.get(Task, task_id) is None:
        return {"error": f"task {task_id} not found"}
    store.update(Task, task_id, {**updates, "updated_at": store.now()})
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


def _edit_summary(fields: list[str]) -> str:
    """"summary", "tags" -> "summary and tags"; three -> "a, b and c"."""
    if len(fields) == 1:
        return fields[0]
    return f"{', '.join(fields[:-1])} and {fields[-1]}"


def update_note(note_id: str, changes: dict) -> dict:
    """Mutate a note's summary/body/tags directly. ONLY callable from chat.

    Appends a role:"user"-attributed trace event to the note, exactly as
    PATCH /notes/{id} does — the user's live chat instruction is the edit.
    """
    updates, error = _validated(_NotePatch, changes)
    if error is not None:
        return error
    assert updates is not None
    note = store.get(Note, note_id)
    if note is None:
        return {"error": f"note {note_id} not found"}
    changed = [f for f in ("summary", "body", "tags") if f in updates]
    args: dict = {"fields": changed}
    if "tags" in updates:
        args["tags"] = {"before": list(note.tags), "after": list(updates["tags"])}
    event = TraceEvent(
        t=store.now(),
        role="user",
        text=f"Edited {_edit_summary(changed)}",
        args=args,
    )
    store.update(
        Note,
        note_id,
        {**updates, "trace": store.array_union([event.model_dump(mode="python")])},
    )
    updated = store.get(Note, note_id)
    assert updated is not None
    return {"note": updated.model_dump(mode="json", exclude={"trace"})}


def search_notes(query: str, limit: int = 20) -> dict:
    """Search notes by substring or tag match over recent notes.

    Naive on purpose (single-user scale, contracts.md): pulls recent notes
    and filters server-side — a query term matches a note when it appears in
    the body, summary, or transcript (case-insensitive) or equals a tag.
    """
    needle = query.strip().lower()
    if not needle:
        return {"error": "query must be non-empty"}
    recent = store.query(Note, limit=500)
    hits = [
        n
        for n in recent
        if needle in (n.body or "").lower()
        or needle in (n.summary or "").lower()
        or needle in (n.transcript or "").lower()
        or needle in n.tags
    ]
    return {
        "notes": [n.model_dump(mode="json", exclude={"trace"}) for n in hits[:limit]]
    }


def start_research(note_id: str) -> dict:
    """Kick off a background deep-research operation for a note.

    Returns {operation_id} on success; the report lands later as a
    `research` note linked back to this one.
    """
    # Late import: memex.agent.research pulls in the aiplatform REST client,
    # which chat sessions should not pay for (or fail on) until asked.
    from memex.agent.research import start_research_operation

    return start_research_operation(note_id)


# Toolset for routine sessions: no direct task writes (neither update_task
# nor create_tasks) — routines propose every task mutation through the
# approval queue (contracts.md task_update / task_create actions).
ROUTINE_TOOLS = [create_note, list_tasks, list_recent_notes, queue_approval]

# Toolset for chat sessions: the routine tools PLUS direct mutation
# (update_task, update_note) and on-demand research — chat mutates directly
# because the user's live instruction is the approval (contracts.md); every
# mutation still lands in the session trace.
CHAT_TOOLS = ROUTINE_TOOLS + [update_task, update_note, search_notes, start_research]
