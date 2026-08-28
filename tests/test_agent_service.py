"""Service seam tests: enrich_capture against the emulator with a stubbed
Vertex call (no LLM-output asserts; the live call is smoke-tested separately).
Skips cleanly when FIRESTORE_EMULATOR_HOST is unset.
"""

import os

import pytest

from memex.ids import new_ulid
from memex.models import ActionItem, Capture, EnrichmentResult, Note, Task
from memex.store import firestore as store

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="FIRESTORE_EMULATOR_HOST not set (Firestore emulator required)",
)


def _make_text_capture(text: str) -> Capture:
    cap = Capture(
        id=new_ulid(),
        created_at=store.now(),
        source="api",
        device_id="dev",
        kind="text",
        text=text,
        status="pending",
    )
    store.put(cap)
    return cap


def test_enrich_capture_text_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from memex.agent import service

    cap = _make_text_capture("call the plumber by friday")
    canned = EnrichmentResult(
        transcript="call the plumber by friday",
        summary="Call the plumber.",
        tags=["home"],
        action_items=[ActionItem(title="Call the plumber", due_hint="by friday")],
    )
    monkeypatch.setattr(service, "enrich_text", lambda text: canned)

    out = service.enrich_capture(cap.id)

    assert "error" not in out
    assert out["capture"]["status"] == "enriched"
    assert out["note"]["kind"] == "capture"
    assert out["note"]["capture_id"] == cap.id
    assert out["note"]["body"] == "call the plumber by friday"
    assert len(out["tasks"]) == 1
    assert out["tasks"][0]["due_hint"] == "by friday"

    stored_cap = store.get(Capture, cap.id)
    assert stored_cap is not None
    assert stored_cap.status == "enriched"
    assert stored_cap.note_id == out["note"]["id"]
    note = store.get(Note, out["note"]["id"])
    assert note is not None
    assert note.task_ids == [out["tasks"][0]["id"]]
    assert len(note.trace) == 2  # user text + model result
    task = store.get(Task, out["tasks"][0]["id"])
    assert task is not None and task.source_note_id == note.id


def test_enrich_capture_failure_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    from memex.agent import service

    cap = _make_text_capture("boom")

    def _explode(text: str) -> EnrichmentResult:
        raise RuntimeError("vertex unavailable")

    monkeypatch.setattr(service, "enrich_text", _explode)
    out = service.enrich_capture(cap.id)

    assert out["error"] == "vertex unavailable"
    assert out["note"] is None
    assert out["tasks"] == []
    stored = store.get(Capture, cap.id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error == "vertex unavailable"


def test_enrich_capture_unknown_id() -> None:
    from memex.agent import service

    out = service.enrich_capture("nope")
    assert out["capture"] is None
    assert "not found" in out["error"]


def test_enrich_capture_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A redelivered Eventarc event must not create a second note/tasks."""
    from memex.agent import service

    cap = _make_text_capture("water the plants")
    canned = EnrichmentResult(
        transcript="water the plants",
        summary="Water the plants.",
        tags=["home"],
        action_items=[ActionItem(title="Water the plants")],
    )
    calls = {"n": 0}

    def fake_enrich(text):
        calls["n"] += 1
        return canned

    monkeypatch.setattr(service, "enrich_text", fake_enrich)

    first = service.enrich_capture(cap.id)
    second = service.enrich_capture(cap.id)

    assert calls["n"] == 1
    assert second.get("deduped") is True
    assert second["note"]["id"] == first["note"]["id"]
    assert [t["id"] for t in second["tasks"]] == [t["id"] for t in first["tasks"]]


def test_enrich_capture_in_flight_returns_in_progress() -> None:
    from memex.agent import service

    cap = _make_text_capture("still being processed")
    store.update(Capture, cap.id, {"status": "processing"})

    out = service.enrich_capture(cap.id)

    assert out.get("in_progress") is True
    assert out["note"] is None
