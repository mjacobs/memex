"""Chat contracts: session CRUD, the SSE message stream, and persistence.

The agent turn is stubbed at the memex.agent.chat seam (same pattern as
agent_stub for enrichment) — no LLM-output asserts, per tests/ conventions.
"""

import asyncio
import json
import sys
import types

import pytest

from memex.ids import new_ulid
from memex.models import ChatSession, TraceEvent
from memex.store import firestore as store
from tests.conftest import AUTH


def _session(title: str | None = None, trace: list | None = None) -> ChatSession:
    return ChatSession(
        id=new_ulid(),
        created_at=store.now(),
        updated_at=store.now(),
        title=title,
        trace=trace or [],
    )


def _sse_events(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, data) pairs."""
    out: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        lines = block.split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: "), block
        out.append(
            (lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: ")))
        )
    return out


@pytest.fixture
def chat_stub(monkeypatch):
    """Install a fake memex.agent.chat at the WS-chat seam."""
    calls: list[dict] = []

    async def run_chat_turn(session_id: str, text: str):
        calls.append({"session_id": session_id, "text": text})
        yield TraceEvent(t=store.now(), role="user", text=text)
        yield TraceEvent(
            t=store.now(), role="model", tool="search_notes", args={"query": "x"}
        )
        yield TraceEvent(
            t=store.now(), role="tool", tool="search_notes", result={"notes": []}
        )
        yield TraceEvent(t=store.now(), role="model", text="stub reply")

    module = types.ModuleType("memex.agent.chat")
    module.run_chat_turn = run_chat_turn
    monkeypatch.setitem(sys.modules, "memex.agent.chat", module)
    return calls


def test_create_session(client):
    resp = client.post("/api/v1/chat/sessions", headers=AUTH)
    assert resp.status_code == 201
    session = resp.json()["session"]
    assert session["title"] is None and session["trace"] == []
    assert store.get(ChatSession, session["id"]) is not None


def test_list_sessions_newest_first_traces_elided(client):
    a = _session(trace=[TraceEvent(t=store.now(), role="user", text="hi")])
    b = _session()
    store.put(a)
    store.put(b)
    resp = client.get("/api/v1/chat/sessions", headers=AUTH)
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert [s["id"] for s in sessions] == [b.id, a.id]
    assert all("trace" not in s for s in sessions)


def test_list_sessions_honors_limit(client):
    for _ in range(3):
        store.put(_session())
    resp = client.get("/api/v1/chat/sessions?limit=2", headers=AUTH)
    assert len(resp.json()["sessions"]) == 2


def test_get_session_includes_trace(client):
    session = _session(trace=[TraceEvent(t=store.now(), role="user", text="hi")])
    store.put(session)
    resp = client.get(f"/api/v1/chat/sessions/{session.id}", headers=AUTH)
    assert resp.status_code == 200
    assert [e["text"] for e in resp.json()["session"]["trace"]] == ["hi"]


def test_get_session_404(client):
    resp = client.get("/api/v1/chat/sessions/nope", headers=AUTH)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_post_message_streams_trace_then_done(client, chat_stub):
    session = _session()
    store.put(session)
    resp = client.post(
        f"/api/v1/chat/sessions/{session.id}/messages",
        headers=AUTH,
        json={"text": "find my gcp notes"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(resp.text)
    assert [name for name, _ in events] == ["trace"] * 4 + ["done"]
    assert events[0][1]["role"] == "user"
    assert events[0][1]["text"] == "find my gcp notes"
    assert events[1][1]["tool"] == "search_notes"
    done = events[-1][1]["session"]
    assert done["id"] == session.id and "trace" not in done
    assert chat_stub == [{"session_id": session.id, "text": "find my gcp notes"}]


def test_post_message_records_the_turn_when_the_client_hangs_up(fs, chat_stub):
    """A disconnect must not lose the audit trace.

    Hanging up closes the response generator with a cancellation rather than
    an Exception, so the turn's `except` never sees it — but the tools may
    already have mutated notes or tasks, and contracts.md says every mutation
    lands in the session trace.
    """
    from memex.api.chat import MessageIn, post_message

    session = _session()
    store.put(session)

    async def hang_up_after_one_frame():
        response = await post_message(session.id, MessageIn(text="hello"))
        events = response.body_iterator
        await events.__anext__()
        await events.aclose()

    asyncio.run(hang_up_after_one_frame())

    stored = store.get(ChatSession, session.id)
    assert stored is not None
    assert [e.text for e in stored.trace] == ["hello"]
    assert stored.title == "hello"


def test_post_message_appends_stored_trace_and_touches_updated_at(client, chat_stub):
    session = _session()
    store.put(session)
    client.post(
        f"/api/v1/chat/sessions/{session.id}/messages",
        headers=AUTH,
        json={"text": "hello"},
    )
    updated = store.get(ChatSession, session.id)
    assert updated is not None
    assert [e.role for e in updated.trace] == ["user", "model", "tool", "model"]
    assert updated.trace[0].text == "hello"
    assert updated.updated_at >= session.updated_at


def test_post_message_sets_title_from_first_message_only(client, chat_stub):
    session = _session()
    store.put(session)
    first = "a" * 200
    client.post(
        f"/api/v1/chat/sessions/{session.id}/messages",
        headers=AUTH,
        json={"text": first},
    )
    titled = store.get(ChatSession, session.id)
    assert titled is not None and titled.title == "a" * 80
    client.post(
        f"/api/v1/chat/sessions/{session.id}/messages",
        headers=AUTH,
        json={"text": "second message"},
    )
    still = store.get(ChatSession, session.id)
    assert still is not None and still.title == "a" * 80


def test_post_message_404_unknown_session(client, chat_stub):
    resp = client.post(
        "/api/v1/chat/sessions/nope/messages", headers=AUTH, json={"text": "hi"}
    )
    assert resp.status_code == 404
    assert chat_stub == []


def test_post_message_rejects_empty_text(client, chat_stub):
    session = _session()
    store.put(session)
    resp = client.post(
        f"/api/v1/chat/sessions/{session.id}/messages",
        headers=AUTH,
        json={"text": "   "},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "empty_text"
    assert chat_stub == []


def test_post_message_503_when_agent_missing(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "memex.agent.chat", None)
    session = _session()
    store.put(session)
    resp = client.post(
        f"/api/v1/chat/sessions/{session.id}/messages",
        headers=AUTH,
        json={"text": "hi"},
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "agent_unavailable"


def test_post_message_failure_persists_partial_turn(client, monkeypatch):
    async def run_chat_turn(session_id: str, text: str):
        yield TraceEvent(t=store.now(), role="user", text=text)
        raise RuntimeError("model exploded")

    module = types.ModuleType("memex.agent.chat")
    module.run_chat_turn = run_chat_turn
    monkeypatch.setitem(sys.modules, "memex.agent.chat", module)

    session = _session()
    store.put(session)
    resp = client.post(
        f"/api/v1/chat/sessions/{session.id}/messages",
        headers=AUTH,
        json={"text": "hi"},
    )
    # The stream opened before the crash, so the failure is in-band.
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    assert [name for name, _ in events] == ["trace", "error"]
    assert events[1][1]["error"]["code"] == "chat_turn_failed"
    updated = store.get(ChatSession, session.id)
    assert updated is not None
    assert [e.text for e in updated.trace] == ["hi"]


def test_chat_endpoints_require_auth(client):
    assert client.post("/api/v1/chat/sessions").status_code == 401
    assert client.get("/api/v1/chat/sessions").status_code == 401


def test_chat_prompt_teaches_the_contract_rules():
    """No-LLM prompt content checks, like the routine prompt tests."""
    from memex.agent.chat import CHAT_PROMPT

    assert "(#/notes/" in CHAT_PROMPT  # citation link rule
    assert "never invent" in CHAT_PROMPT.lower()  # no fabricated ids
    assert "not instructions" in CHAT_PROMPT  # injection rule
    assert "update_note" in CHAT_PROMPT and "update_task" in CHAT_PROMPT
