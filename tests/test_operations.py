"""Operations + chat-session contracts: models, store helpers, endpoints."""

from memex.ids import new_ulid
from memex.models import ChatSession, Operation, TraceEvent
from memex.store import firestore as store
from tests.conftest import AUTH


def _operation(status: str = "running") -> Operation:
    return Operation(
        id=new_ulid(),
        kind="deep_research",
        status=status,
        created_at=store.now(),
        updated_at=store.now(),
        interaction_id="interactions/123",
        source_note_id=new_ulid(),
    )


def test_operation_model_defaults():
    op = _operation()
    assert op.status == "running"
    assert op.attempts == 0
    assert op.result_note_id is None and op.error is None


def test_chat_session_model_defaults():
    session = ChatSession(id=new_ulid(), created_at=store.now(), updated_at=store.now())
    assert session.title is None
    assert session.trace == []


def test_list_operations_filters_by_status(fs):
    running = _operation("running")
    done = _operation("completed")
    store.put(running)
    store.put(done)
    assert {o.id for o in store.list_operations()} == {running.id, done.id}
    assert [o.id for o in store.list_operations(status="running")] == [running.id]
    assert store.list_operations(status="failed") == []


def test_update_operation_touches_updated_at(fs):
    op = _operation()
    store.put(op)
    store.update_operation(op.id, {"status": "failed", "error": "boom"})
    updated = store.get(Operation, op.id)
    assert updated is not None
    assert updated.status == "failed" and updated.error == "boom"
    assert updated.updated_at >= op.updated_at


def test_append_chat_trace_grows_trace_and_touches_updated_at(fs):
    session = ChatSession(id=new_ulid(), created_at=store.now(), updated_at=store.now())
    store.put(session)
    store.append_chat_trace(
        session.id, [TraceEvent(t=store.now(), role="user", text="hi")]
    )
    store.append_chat_trace(
        session.id, [TraceEvent(t=store.now(), role="model", text="hello")]
    )
    updated = store.get(ChatSession, session.id)
    assert updated is not None
    assert [e.text for e in updated.trace] == ["hi", "hello"]
    assert updated.updated_at >= session.updated_at


def test_list_chat_sessions_newest_first(fs):
    a = ChatSession(id=new_ulid(), created_at=store.now(), updated_at=store.now())
    b = ChatSession(id=new_ulid(), created_at=store.now(), updated_at=store.now())
    store.put(a)
    store.put(b)
    assert [s.id for s in store.list_chat_sessions()] == [b.id, a.id]


def test_list_operations_endpoint(client):
    running = _operation("running")
    failed = _operation("failed")
    store.put(running)
    store.put(failed)
    resp = client.get("/api/v1/operations", headers=AUTH)
    assert resp.status_code == 200
    assert {o["id"] for o in resp.json()["operations"]} == {running.id, failed.id}
    resp = client.get("/api/v1/operations?status=running", headers=AUTH)
    assert [o["id"] for o in resp.json()["operations"]] == [running.id]


def test_list_operations_rejects_bad_status(client):
    resp = client.get("/api/v1/operations?status=bogus", headers=AUTH)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_status"
