"""run_routine outcome contract: which sessions count as succeeded vs failed.

The routine prompts tell the agent to skip the note when there is nothing to
report, so a completed session with no note and no approvals is a quiet
success — not an incomplete run. Approvals without a note still fail: the
prompts require every queued proposal to be documented in the note, so its
absence means the session died partway.
"""

import pytest

from memex.models import RoutineRun
from memex.store import firestore as store


class _SessionResult:
    def __init__(self, summary: str) -> None:
        self.summary = summary
        self.trace = []


def _run_with(monkeypatch: pytest.MonkeyPatch, *, notes: list[str], approvals: list[str], summary: str = "done") -> dict:
    from memex.agent import routines as routines_mod
    from memex.agent import service, tools

    async def fake_session(routine: str) -> _SessionResult:
        ctx = tools._run_context.get()
        ctx.note_ids.extend(notes)
        ctx.approval_ids.extend(approvals)
        return _SessionResult(summary)

    monkeypatch.setattr(routines_mod, "run_routine_session", fake_session)
    return service.run_routine("daily_review")


def test_quiet_session_is_a_success(fs, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run_with(monkeypatch, notes=[], approvals=[], summary="No task changes needed.")
    assert out["status"] == "succeeded"
    assert out["error"] is None
    assert out["note_id"] is None
    assert out["summary"] == "No task changes needed."
    stored = store.get(RoutineRun, out["id"])
    assert stored is not None and stored.status == "succeeded"


def test_session_with_note_succeeds(fs, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run_with(monkeypatch, notes=["01NOTE"], approvals=["01APPR"])
    assert out["status"] == "succeeded"
    assert out["note_id"] == "01NOTE"
    assert out["approval_ids"] == ["01APPR"]


def test_approvals_without_note_fail(fs, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run_with(monkeypatch, notes=[], approvals=["01APPR"])
    assert out["status"] == "failed"
    assert "queued approvals but produced no note" in out["error"]
    assert out["approval_ids"] == ["01APPR"]


def test_session_exception_marks_failed(fs, monkeypatch: pytest.MonkeyPatch) -> None:
    from memex.agent import routines as routines_mod
    from memex.agent import service

    async def boom(routine: str):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(routines_mod, "run_routine_session", boom)
    out = service.run_routine("daily_review")
    assert out["status"] == "failed"
    assert "model unavailable" in out["error"]
