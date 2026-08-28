"""Contract-level tool I/O against the Firestore emulator.

Skips cleanly when FIRESTORE_EMULATOR_HOST is unset. Start one with:
    gcloud emulators firestore start --host-port=127.0.0.1:8790
"""

import os

import pytest

from memex.agent import tools
from memex.models import Approval, Note, Task
from memex.store import firestore as store

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="FIRESTORE_EMULATOR_HOST not set (Firestore emulator required)",
)


def test_create_note_returns_note_id_and_persists() -> None:
    out = tools.create_note(
        kind="capture",
        body="remember the milk",
        summary="Buy milk.",
        tags=["errands"],
        transcript=None,
        capture_id=None,
    )
    note = store.get(Note, out["note_id"])
    assert note is not None
    assert note.body == "remember the milk"
    assert note.kind == "capture"
    assert note.tags == ["errands"]


def test_create_tasks_links_source_note() -> None:
    note_id = tools.create_note(
        kind="capture", body="b", summary="s", tags=[]
    )["note_id"]
    out = tools.create_tasks(
        [{"title": "buy milk"}, {"title": "call mom"}],
        source_note_id=note_id,
    )
    assert len(out["task_ids"]) == 2
    task = store.get(Task, out["task_ids"][0])
    assert task is not None
    assert task.title == "buy milk"
    assert task.status == "open"
    assert task.source_note_id == note_id
    note = store.get(Note, note_id)
    assert note is not None
    assert note.task_ids == out["task_ids"]


def test_list_tasks_filters_by_status() -> None:
    note_id = tools.create_note(kind="capture", body="b", summary="s", tags=[])["note_id"]
    tid = tools.create_tasks([{"title": "listable"}], source_note_id=note_id)["task_ids"][0]
    open_ids = {t["id"] for t in tools.list_tasks(status="open")["tasks"]}
    assert tid in open_ids
    done_ids = {t["id"] for t in tools.list_tasks(status="done")["tasks"]}
    assert tid not in done_ids


def test_update_task_mutates_and_touches_updated_at() -> None:
    note_id = tools.create_note(kind="capture", body="b", summary="s", tags=[])["note_id"]
    tid = tools.create_tasks([{"title": "old"}], source_note_id=note_id)["task_ids"][0]
    before = store.get(Task, tid)
    assert before is not None
    out = tools.update_task(tid, {"title": "new", "status": "done"})
    assert "error" not in out
    assert out["task"]["title"] == "new"
    assert out["task"]["status"] == "done"
    after = store.get(Task, tid)
    assert after is not None
    assert after.updated_at >= before.updated_at


def test_update_task_rejects_disallowed_fields_and_missing_task() -> None:
    assert "error" in tools.update_task("nonexistent", {"title": "x"})
    note_id = tools.create_note(kind="capture", body="b", summary="s", tags=[])["note_id"]
    tid = tools.create_tasks([{"title": "t"}], source_note_id=note_id)["task_ids"][0]
    out = tools.update_task(tid, {"id": "evil"})
    assert "error" in out


def test_queue_approval_validates_action_contract() -> None:
    bad = tools.queue_approval({"type": "task_delete", "task_id": "x"}, reason="r")
    assert "error" in bad
    good = tools.queue_approval(
        {"type": "task_update", "task_id": "abc", "changes": {"status": "done"}},
        reason="looks finished",
    )
    approval = store.get(Approval, good["approval_id"])
    assert approval is not None
    assert approval.status == "pending"
    assert approval.action.type == "task_update"
    assert approval.routine_run_id is None


def test_run_context_attributes_and_collects_ids() -> None:
    with tools.run_context("run123") as ctx:
        note_id = tools.create_note(kind="review", body="b", summary="s", tags=[])["note_id"]
        approval_id = tools.queue_approval(
            {"type": "task_create", "task": {"title": "proposed"}}, reason="new"
        )["approval_id"]
    assert ctx.note_ids == [note_id]
    assert ctx.approval_ids == [approval_id]
    note = store.get(Note, note_id)
    assert note is not None and note.routine_run_id == "run123"
    approval = store.get(Approval, approval_id)
    assert approval is not None and approval.routine_run_id == "run123"


def test_list_recent_notes_excludes_trace_and_respects_days() -> None:
    tools.create_note(kind="capture", body="fresh", summary="s", tags=[])
    out = tools.list_recent_notes(limit=10, days=1)
    assert out["notes"], "expected at least the note just created"
    assert all("trace" not in n for n in out["notes"])
