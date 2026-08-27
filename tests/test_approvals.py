"""Approval queue: listing, approve-applies-action, reject."""

from memex.ids import new_ulid
from memex.models import Approval, Task
from memex.store import firestore as store
from tests.conftest import AUTH


def _make_task(title: str = "task") -> Task:
    task = Task(
        id=new_ulid(),
        title=title,
        created_at=store.now(),
        updated_at=store.now(),
    )
    store.put(task)
    return task


def _make_approval(action: dict, status: str = "pending") -> Approval:
    approval = Approval(
        id=new_ulid(),
        created_at=store.now(),
        status=status,
        action=action,
        reason="stub reason",
    )
    store.put(approval)
    return approval


def test_list_defaults_to_pending(client, fs):
    pending = _make_approval(
        {"type": "task_create", "task": {"title": "a"}}, status="pending"
    )
    _make_approval({"type": "task_create", "task": {"title": "b"}}, status="rejected")
    r = client.get("/api/v1/approvals", headers=AUTH)
    assert [a["id"] for a in r.json()["approvals"]] == [pending.id]


def test_approve_applies_task_update(client, fs):
    task = _make_task()
    approval = _make_approval(
        {
            "type": "task_update",
            "task_id": task.id,
            "changes": {"status": "done", "tags": ["reviewed"]},
        }
    )
    r = client.post(f"/api/v1/approvals/{approval.id}/approve", headers=AUTH)
    assert r.status_code == 200
    body = r.json()["approval"]
    assert body["status"] == "approved"
    assert body["resolved_at"] is not None
    assert task.id in body["result"]

    updated = store.get(Task, task.id)
    assert updated is not None
    assert updated.status == "done"
    assert updated.tags == ["reviewed"]
    assert updated.updated_at > task.updated_at


def test_approve_applies_task_create(client, fs):
    approval = _make_approval(
        {
            "type": "task_create",
            "task": {"title": "new task", "due_hint": "by Friday", "tags": ["x"]},
        }
    )
    r = client.post(f"/api/v1/approvals/{approval.id}/approve", headers=AUTH)
    assert r.status_code == 200

    tasks = store.query(Task, filters=[("status", "==", "open")], limit=10)
    assert len(tasks) == 1
    assert tasks[0].title == "new task"
    assert tasks[0].due_hint == "by Friday"


def test_reject(client, fs):
    approval = _make_approval({"type": "task_create", "task": {"title": "a"}})
    r = client.post(f"/api/v1/approvals/{approval.id}/reject", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["approval"]["status"] == "rejected"

    # already resolved -> 409
    r2 = client.post(f"/api/v1/approvals/{approval.id}/approve", headers=AUTH)
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "already_resolved"


def test_approve_missing_approval_404(client, fs):
    r = client.post("/api/v1/approvals/nope/approve", headers=AUTH)
    assert r.status_code == 404
