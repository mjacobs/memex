"""Chat-only agent tools (update_note, search_notes, start_research).

Runs against the in-memory fake via the fs fixture — the emulator-gated
tool tests live in test_agent_tools.py.
"""

from memex.agent import tools
from memex.ids import new_ulid
from memex.models import Note
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
