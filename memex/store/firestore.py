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
from memex.models import Approval, Capture, Note, RoutineRun, Task

COLLECTIONS: dict[type[BaseModel], str] = {
    Capture: "captures",
    Note: "notes",
    Task: "tasks",
    Approval: "approvals",
    RoutineRun: "routine_runs",
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
