"""Notes and tasks listing, pagination, and task patching."""

from memex.ids import new_ulid
from memex.models import Note, Task
from memex.store import firestore as store
from tests.conftest import AUTH


def _make_note(i: int, kind: str = "capture", tags: list[str] | None = None) -> Note:
    note = Note(
        id=new_ulid(),
        created_at=store.now(),
        kind=kind,
        body=f"note {i}",
        summary=f"summary {i}",
        tags=tags or [],
        trace=[{"t": store.now(), "role": "model", "text": "hi"}],
    )
    store.put(note)
    return note


def _make_task(title: str, status: str = "open") -> Task:
    task = Task(
        id=new_ulid(),
        title=title,
        status=status,
        created_at=store.now(),
        updated_at=store.now(),
    )
    store.put(task)
    return task


def test_notes_newest_first_and_paginated(client, fs):
    notes = [_make_note(i) for i in range(5)]
    r = client.get("/api/v1/notes", params={"limit": 3}, headers=AUTH)
    assert r.status_code == 200
    page1 = r.json()["notes"]
    expected = [n.id for n in reversed(notes)]
    assert [n["id"] for n in page1] == expected[:3]
    assert all("trace" not in n for n in page1)  # traces elided on list

    r2 = client.get(
        "/api/v1/notes", params={"limit": 3, "before": page1[-1]["id"]}, headers=AUTH
    )
    page2 = r2.json()["notes"]
    assert [n["id"] for n in page2] == expected[3:]


def test_notes_filter_by_kind_and_tag(client, fs):
    _make_note(0, kind="capture", tags=["errands"])
    digest = _make_note(1, kind="digest", tags=["daily"])
    tagged = _make_note(2, kind="capture", tags=["errands", "home"])

    r = client.get("/api/v1/notes", params={"kind": "digest"}, headers=AUTH)
    assert [n["id"] for n in r.json()["notes"]] == [digest.id]

    r = client.get("/api/v1/notes", params={"tag": "home"}, headers=AUTH)
    assert [n["id"] for n in r.json()["notes"]] == [tagged.id]


def test_note_detail_includes_trace(client, fs):
    note = _make_note(0)
    r = client.get(f"/api/v1/notes/{note.id}", headers=AUTH)
    assert r.status_code == 200
    assert len(r.json()["note"]["trace"]) == 1

    r404 = client.get("/api/v1/notes/nope", headers=AUTH)
    assert r404.status_code == 404


def test_tasks_default_open(client, fs):
    open_task = _make_task("open one")
    done_task = _make_task("done one", status="done")

    r = client.get("/api/v1/tasks", headers=AUTH)
    assert [t["id"] for t in r.json()["tasks"]] == [open_task.id]

    r = client.get("/api/v1/tasks", params={"status": "done"}, headers=AUTH)
    assert [t["id"] for t in r.json()["tasks"]] == [done_task.id]

    r = client.get("/api/v1/tasks", params={"status": "bogus"}, headers=AUTH)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_status"


def test_patch_task(client, fs):
    task = _make_task("original")
    r = client.patch(
        f"/api/v1/tasks/{task.id}",
        json={"status": "done", "title": "renamed", "tags": ["x"]},
        headers=AUTH,
    )
    assert r.status_code == 200
    patched = r.json()["task"]
    assert patched["status"] == "done"
    assert patched["title"] == "renamed"
    assert patched["tags"] == ["x"]
    assert patched["updated_at"] >= patched["created_at"]


def test_patch_task_404_and_empty(client, fs):
    r = client.patch("/api/v1/tasks/nope", json={"status": "done"}, headers=AUTH)
    assert r.status_code == 404

    task = _make_task("t")
    r = client.patch(f"/api/v1/tasks/{task.id}", json={}, headers=AUTH)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "empty_update"
