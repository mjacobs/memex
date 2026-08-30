"""Thin Firestore CRUD over the contract models.

Both the API (W2) and the agent tools (W3) go through these helpers —
no other module talks to Firestore directly. Collections and shapes per
docs/contracts.md. Uses the emulator when FIRESTORE_EMULATOR_HOST is set
(the google-cloud-firestore client honors it natively).
"""

import logging
from datetime import UTC, datetime
from functools import lru_cache

from google.api_core.exceptions import AlreadyExists, FailedPrecondition
from google.cloud import firestore
from pydantic import BaseModel

from memex.config import settings
from memex.models import (
    Approval,
    Capture,
    ChatSession,
    Note,
    Operation,
    RoutineRun,
    Task,
    TraceEvent,
)

logger = logging.getLogger(__name__)

# Retries for one contended terminal note write (see settle_note_research).
_SETTLE_ATTEMPTS = 3

COLLECTIONS: dict[type[BaseModel], str] = {
    Capture: "captures",
    Note: "notes",
    Task: "tasks",
    Approval: "approvals",
    RoutineRun: "routine_runs",
    Operation: "operations",
    ChatSession: "chat_sessions",
}


@lru_cache
def db() -> firestore.Client:
    return firestore.Client(project=settings().project)


def now() -> datetime:
    return datetime.now(UTC)


def put(entity: BaseModel) -> None:
    coll = COLLECTIONS[type(entity)]
    db().collection(coll).document(entity.id).set(entity.model_dump(mode="python"))


def create(entity: BaseModel) -> bool:
    """Write a doc only if its id is still free; False when it is taken.

    `put` is an unconditional set, which is what a caller minting a fresh id
    wants. It is the wrong write for an id agreed on in advance — a report
    note's id is reserved on its operation before the note exists, and an
    at-least-once redelivery replays that write, so `put` would overwrite
    whatever the document has become since.
    """
    coll = COLLECTIONS[type(entity)]
    try:
        db().collection(coll).document(entity.id).create(
            entity.model_dump(mode="python")
        )
    except AlreadyExists:
        return False
    return True


def get[M: BaseModel](model: type[M], doc_id: str) -> M | None:
    snap = db().collection(COLLECTIONS[model]).document(doc_id).get()
    return model.model_validate(snap.to_dict()) if snap.exists else None


def update[M: BaseModel](model: type[M], doc_id: str, changes: dict) -> None:
    db().collection(COLLECTIONS[model]).document(doc_id).update(changes)


def array_union(values: list) -> object:
    """Sentinel value for update(): append to an array field server-side.

    Lets a caller grow a list (a note's trace, say) without the
    read-modify-write round trip, which two concurrent writers can
    interleave and silently drop one of the appended entries.
    """
    return firestore.ArrayUnion(values)


def delete[M: BaseModel](model: type[M], doc_id: str) -> None:
    """Hard-delete a doc. Callers own any dangling references (see routes)."""
    db().collection(COLLECTIONS[model]).document(doc_id).delete()


def query[M: BaseModel](
    model: type[M],
    *,
    filters: list[tuple[str, str, object]] | None = None,
    order_by: str = "id",
    descending: bool = True,
    limit: int = 50,
    before: str | None = None,
) -> list[M]:
    """List docs, newest-first by ULID id by default (`before` paginates)."""
    q = db().collection(COLLECTIONS[model])
    for field, op, value in filters or []:
        q = q.where(filter=firestore.FieldFilter(field, op, value))
    direction = firestore.Query.DESCENDING if descending else firestore.Query.ASCENDING
    q = q.order_by(order_by, direction=direction)
    if before is not None:
        q = q.start_after({order_by: before})
    return [model.model_validate(s.to_dict()) for s in q.limit(limit).stream()]


# --- operations / chat_sessions (agentic-v2) -------------------------------
# Named helpers so the LRO queue and chat turn paths share one spelling of
# "touch updated_at" and "append without read-modify-write".


def list_operations(status: str | None = None, limit: int = 50) -> list[Operation]:
    """List operations newest-first, optionally only one status."""
    filters = [("status", "==", status)] if status is not None else []
    return query(Operation, filters=filters, limit=limit)


def update_operation(operation_id: str, changes: dict) -> None:
    """Apply changes to an operation, touching updated_at."""
    update(Operation, operation_id, {**changes, "updated_at": now()})


def transition_operation(
    operation_id: str, expected_status: str, changes: dict
) -> bool:
    """Compare-and-set one operation transition; True when this caller won.

    Cloud Tasks delivery is at-least-once, so two deliveries of the same poll
    can both read an operation as `running` and both try to advance it. The
    write carries a last_update_time precondition, so it only lands on the
    document exactly as it was read: the second delivery gets False and
    leaves the operation alone.
    """
    ref = db().collection(COLLECTIONS[Operation]).document(operation_id)
    snap = ref.get()
    if not snap.exists or (snap.to_dict() or {}).get("status") != expected_status:
        return False
    try:
        ref.update(
            {**changes, "updated_at": now()},
            option=db().write_option(last_update_time=snap.update_time),
        )
    except FailedPrecondition:
        return False
    return True


def reserve_operation_result(operation_id: str, note_id: str) -> bool:
    """Claim the right to write this operation's result; True if we won.

    `transition_operation` guards a status change, but reserving a result and
    settling are two writes, and between them the operation is still running
    with a status a second delivery reads as its own to advance. Conditioning
    on result_note_id being unset makes the reservation itself exclusive, so
    only one delivery ever writes the report.
    """
    ref = db().collection(COLLECTIONS[Operation]).document(operation_id)
    snap = ref.get()
    if not snap.exists:
        return False
    current = snap.to_dict() or {}
    if current.get("status") != "running" or current.get("result_note_id") is not None:
        return False
    try:
        ref.update(
            {"result_note_id": note_id, "updated_at": now()},
            option=db().write_option(last_update_time=snap.update_time),
        )
    except FailedPrecondition:
        return False
    return True


def claim_note_research(note_id: str, operation_id: str) -> bool:
    """Compare-and-set a note into research_status="running"; True if we won.

    The 409 on POST /notes/{id}/research is a read followed by a start, and a
    double tap can slip between them — two interactions, two bills. Claiming
    the note first with a last_update_time precondition makes exactly one
    caller the winner, and the loser never creates an interaction at all.

    The claim records which operation holds it, so a late write from a run
    that has since been superseded cannot clear it (see settle_note_research).
    """
    ref = db().collection(COLLECTIONS[Note]).document(note_id)
    snap = ref.get()
    if not snap.exists or (snap.to_dict() or {}).get("research_status") == "running":
        return False
    try:
        ref.update(
            {"research_status": "running", "research_operation_id": operation_id},
            option=db().write_option(last_update_time=snap.update_time),
        )
    except FailedPrecondition:
        return False
    return True


def settle_note_research(
    note_id: str, operation_id: str, status: str | None, extra: dict | None = None
) -> bool:
    """Write a terminal research status, but only for the run that owns it.

    Two deliveries of one poll can both pass the operation's status check, and
    the slower one can land after the user has started a *second* run. Without
    this guard that stale write clears the new run's claim, the note reads as
    free, and the next tap buys an interaction that is already running.

    Returns False when the note is gone or another run owns it — neither is an
    error, both mean this write has nothing to say. Any other failure raises,
    because silently dropping it would strand the note as running.
    """
    ref = db().collection(COLLECTIONS[Note]).document(note_id)
    # A concurrent write to the note is contention, not supersession. Losing
    # the precondition to an unrelated edit and reporting "not mine" would
    # leave the note running with the operation already settled, which nothing
    # reconciles — so re-read and try again, and only give up when the note is
    # gone or another run genuinely owns it.
    for _ in range(_SETTLE_ATTEMPTS):
        snap = ref.get()
        if not snap.exists:
            return False
        current = snap.to_dict() or {}
        owner = current.get("research_operation_id")
        if owner not in (operation_id, None):
            logger.info(
                "note %s is owned by run %s; not settling it for %s",
                note_id,
                owner,
                operation_id,
            )
            return False
        try:
            ref.update(
                {"research_status": status, **(extra or {})},
                option=db().write_option(last_update_time=snap.update_time),
            )
        except FailedPrecondition:
            continue
        return True
    # Out of attempts under sustained contention. Raising is deliberate: the
    # caller must not settle its operation against a note it failed to write.
    raise FailedPrecondition(
        f"note {note_id} kept changing while run {operation_id} tried to settle it"
    )


def list_chat_sessions(limit: int = 20) -> list[ChatSession]:
    """List chat sessions newest-first (traces included; callers elide)."""
    return query(ChatSession, limit=limit)


def append_chat_trace(session_id: str, events: list[TraceEvent]) -> None:
    """Append turn events to a session's stored trace, touching updated_at.

    Server-side array append (see array_union) so a slow turn and a fast one
    landing together never drop each other's events.
    """
    update(
        ChatSession,
        session_id,
        {
            "trace": array_union([e.model_dump(mode="python") for e in events]),
            "updated_at": now(),
        },
    )
