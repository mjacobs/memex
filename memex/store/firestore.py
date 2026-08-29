"""Thin Firestore CRUD over the contract models.

Both the API (W2) and the agent tools (W3) go through these helpers —
no other module talks to Firestore directly. Collections and shapes per
docs/contracts.md. Uses the emulator when FIRESTORE_EMULATOR_HOST is set
(the google-cloud-firestore client honors it natively).
"""

from datetime import UTC, datetime
from functools import lru_cache

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
