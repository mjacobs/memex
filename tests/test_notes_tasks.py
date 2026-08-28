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


def test_patch_note_updates_fields_and_traces_the_edit(client, fs):
    note = _make_note(0, tags=["old"])
    r = client.patch(
        f"/api/v1/notes/{note.id}",
        json={"summary": "my words", "tags": ["new", "home"]},
        headers=AUTH,
    )
    assert r.status_code == 200
    patched = r.json()["note"]
    assert patched["summary"] == "my words"
    assert patched["tags"] == ["new", "home"]
    assert patched["body"] == "note 0"  # untouched

    event = patched["trace"][-1]
    assert event["role"] == "user"
    assert event["text"] == "Edited summary and tags"
    assert event["args"]["fields"] == ["summary", "tags"]
    assert event["args"]["tags"] == {"before": ["old"], "after": ["new", "home"]}
    # the model's original trace is preserved ahead of the user edit
    assert patched["trace"][0]["role"] == "model"


def test_patch_note_body_only_trace_text(client, fs):
    note = _make_note(0)
    r = client.patch(
        f"/api/v1/notes/{note.id}", json={"body": "rewritten"}, headers=AUTH
    )
    assert r.status_code == 200
    event = r.json()["note"]["trace"][-1]
    assert event["text"] == "Edited body"
    assert event["args"] == {"fields": ["body"]}


def test_patch_note_404_empty_and_unknown_fields(client, fs):
    r = client.patch("/api/v1/notes/nope", json={"summary": "x"}, headers=AUTH)
    assert r.status_code == 404

    note = _make_note(0)
    r = client.patch(f"/api/v1/notes/{note.id}", json={}, headers=AUTH)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "empty_update"

    r = client.patch(f"/api/v1/notes/{note.id}", json={"kind": "digest"}, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"

    r = client.patch(f"/api/v1/notes/{note.id}", json={"tags": "home"}, headers=AUTH)
    assert r.status_code == 422

    r = client.patch(f"/api/v1/notes/{note.id}", json={"summary": None}, headers=AUTH)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_patch"

    # none of the rejected calls touched the doc or appended a trace event
    unchanged = client.get(f"/api/v1/notes/{note.id}", headers=AUTH).json()["note"]
    assert unchanged["summary"] == "summary 0"
    assert unchanged["kind"] == "capture"
    assert len(unchanged["trace"]) == 1


def test_delete_note_leaves_tasks_and_capture_alone(client, fs, agent_stub):
    r = client.post("/api/v1/capture", json={"text": "buy milk"}, headers=AUTH)
    assert r.status_code == 201
    note_id = r.json()["note"]["id"]
    capture_id = r.json()["capture"]["id"]
    task_id = r.json()["tasks"][0]["id"]

    d = client.delete(f"/api/v1/notes/{note_id}", headers=AUTH)
    assert d.status_code == 200
    assert d.json() == {"deleted": note_id}

    assert client.get(f"/api/v1/notes/{note_id}", headers=AUTH).status_code == 404
    assert client.delete(f"/api/v1/notes/{note_id}", headers=AUTH).status_code == 404
    assert note_id not in [n["id"] for n in client.get("/api/v1/notes", headers=AUTH).json()["notes"]]

    # the spawned task and the originating capture survive, dangling ref and all
    tasks = client.get("/api/v1/tasks", headers=AUTH).json()["tasks"]
    task = next(t for t in tasks if t["id"] == task_id)
    assert task["source_note_id"] == note_id
    capture = client.get(f"/api/v1/captures/{capture_id}", headers=AUTH).json()["capture"]
    assert capture["note_id"] == note_id


def test_note_edit_and_delete_require_auth(client, fs):
    note = _make_note(0)
    assert client.patch(f"/api/v1/notes/{note.id}", json={"summary": "x"}).status_code == 401
    assert client.delete(f"/api/v1/notes/{note.id}").status_code == 401


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


def test_patch_task_rejects_null_required_fields(client, fs):
    task = _make_task("keep me intact")
    r = client.patch(
        f"/api/v1/tasks/{task.id}", json={"status": None, "title": None}, headers=AUTH
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_patch"
    # the doc is untouched and still readable
    r2 = client.get("/api/v1/tasks", headers=AUTH)
    assert any(t["id"] == task.id for t in r2.json()["tasks"])


def test_patch_task_invalid_value_400_not_500(client, fs):
    task = _make_task("status stays valid")
    r = client.patch(
        f"/api/v1/tasks/{task.id}", json={"status": "closed"}, headers=AUTH
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_patch"


def test_patch_task_can_clear_due_at(client, fs):
    task = _make_task("clearable due date")
    r = client.patch(
        f"/api/v1/tasks/{task.id}", json={"due_at": None}, headers=AUTH
    )
    assert r.status_code == 200
    assert r.json()["task"]["due_at"] is None
