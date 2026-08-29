"""Chat-only agent tools (update_note, search_notes, start_research).

Runs against the in-memory fake via the fs fixture — the emulator-gated
tool tests live in test_agent_tools.py.
"""

from memex.agent import tools
from memex.ids import new_ulid
from memex.models import Note, Task
from memex.store import firestore as store


def _note(**overrides) -> Note:
    fields = {
        "id": new_ulid(),
        "created_at": store.now(),
        "kind": "capture",
        "body": "remember the milk",
        "summary": "Buy milk.",
        "tags": ["errands"],
        **overrides,
    }
    note = Note(**fields)
    store.put(note)
    return note


def test_chat_tools_extend_routine_tools():
    assert tools.CHAT_TOOLS[: len(tools.ROUTINE_TOOLS)] == tools.ROUTINE_TOOLS
    extras = tools.CHAT_TOOLS[len(tools.ROUTINE_TOOLS) :]
    assert extras == [
        tools.update_task,
        tools.update_note,
        tools.search_notes,
        tools.start_research,
    ]


def _task(**overrides) -> Task:
    fields = {
        "id": new_ulid(),
        "title": "write the spec",
        "created_at": store.now(),
        "updated_at": store.now(),
        **overrides,
    }
    task = Task(**fields)
    store.put(task)
    return task


def test_update_task_validates_values_before_writing(fs):
    """An untyped `changes` dict is the model's word, not a contract.

    A plausible-but-wrong status has to be refused up front — persisting it
    and only failing on read-back leaves an unloadable task behind.
    """
    task = _task()
    assert "error" in tools.update_task(task.id, {"status": "completed"})
    assert "error" in tools.update_task(task.id, {"title": None})
    assert "error" in tools.update_task(task.id, {"tags": "groceries"})
    stored = store.get(Task, task.id)
    assert stored is not None
    assert stored.status == "open" and stored.title == "write the spec"

    # Tags are normalized on the way in, like every other tag write.
    out = tools.update_task(task.id, {"tags": ["Read Later", "read later"]})
    assert out["task"]["tags"] == ["read-later"]


def test_update_note_edits_and_appends_user_trace_event(fs):
    note = _note()
    out = tools.update_note(note.id, {"summary": "Milk!", "tags": ["Groceries"]})
    assert "error" not in out
    assert out["note"]["summary"] == "Milk!"
    assert out["note"]["tags"] == ["groceries"]  # normalized
    assert "trace" not in out["note"]
    updated = store.get(Note, note.id)
    assert updated is not None
    event = updated.trace[-1]
    assert event.role == "user"
    assert event.text == "Edited summary and tags"
    assert event.args["tags"] == {"before": ["errands"], "after": ["groceries"]}


def test_update_note_rejects_bad_input(fs):
    assert "error" in tools.update_note("nonexistent", {"summary": "x"})
    note = _note()
    assert "error" in tools.update_note(note.id, {"kind": "digest"})
    assert "error" in tools.update_note(note.id, {})
    assert "error" in tools.update_note(note.id, {"summary": None})


def test_patches_refuse_to_blank_a_field(fs):
    """An empty string is not an edit — it would erase the field."""
    task = _task()
    assert "error" in tools.update_task(task.id, {"title": ""})
    assert "error" in tools.update_task(task.id, {"title": "   "})
    stored = store.get(Task, task.id)
    assert stored is not None and stored.title == "write the spec"

    note = _note()
    assert "error" in tools.update_note(note.id, {"body": ""})
    assert "error" in tools.update_note(note.id, {"summary": "  "})
    kept = store.get(Note, note.id)
    assert kept is not None
    assert kept.body == "remember the milk" and kept.summary == "Buy milk."


def test_search_notes_matches_substring_and_tag(fs):
    milk = _note()
    tagged = _note(body="water the plants", summary="Garden.", tags=["home"])
    out = tools.search_notes("MILK")
    assert [n["id"] for n in out["notes"]] == [milk.id]
    out = tools.search_notes("home")
    assert [n["id"] for n in out["notes"]] == [tagged.id]
    assert tools.search_notes("no-such-thing")["notes"] == []
    assert all("trace" not in n for n in tools.search_notes("milk")["notes"])


def test_search_notes_rejects_empty_query(fs):
    assert "error" in tools.search_notes("   ")


def test_start_research_delegates_to_research_module(fs, monkeypatch):
    import memex.agent.research as research_mod

    note = _note()
    monkeypatch.setattr(
        research_mod,
        "start_research_operation",
        lambda note_id: {"operation_id": f"op-for-{note_id}"},
    )
    assert tools.start_research(note.id) == {"operation_id": f"op-for-{note.id}"}
