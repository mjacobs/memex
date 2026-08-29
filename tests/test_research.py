"""Deep-research lifecycle: start-on-tag, poll transitions, internal route.

The aiplatform HTTP calls and Cloud Tasks enqueues are mocked — contract
tests assert the operation/notes plumbing, not Deep Research output.
"""

import json

import httpx
import pytest

from memex.agent import research
from memex.ids import new_ulid
from memex.models import ActionItem, EnrichmentResult, Note, Operation
from memex.store import firestore as store
from tests.conftest import AUTH


def _make_note(
    tags: list[str] | None = None, body: str = "look into rust pinning"
) -> Note:
    note = Note(
        id=new_ulid(),
        created_at=store.now(),
        kind="capture",
        body=body,
        summary="Look into rust pinning.",
        tags=tags or [],
    )
    store.put(note)
    return note


def _make_operation(**overrides) -> Operation:
    fields = {
        "id": new_ulid(),
        "kind": "deep_research",
        "created_at": store.now(),
        "updated_at": store.now(),
        "interaction_id": "i-123",
        "source_note_id": new_ulid(),
        **overrides,
    }
    op = Operation(**fields)
    store.put(op)
    return op


@pytest.fixture
def enqueued(monkeypatch):
    """Record poll enqueues instead of talking to Cloud Tasks."""
    calls: list[str] = []

    def _enqueue_poll(operation_id: str, delay_seconds: int = 30) -> None:
        calls.append(operation_id)

    monkeypatch.setattr(research, "_enqueue_poll", _enqueue_poll)
    return calls


# --- interaction create (httpx mocked) -------------------------------------


def _mock_create(monkeypatch, response_factory) -> dict:
    """Point _create_interaction at a mock transport; record the request."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content)
        return response_factory()

    real_client = httpx.Client
    monkeypatch.setattr(
        research.httpx,
        "Client",
        lambda *a, **kw: real_client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(research, "_auth_headers", dict)
    return seen


# The live create response (2026-08-28): SSE, id on the first event, and the
# stream stays open for the length of the research run. A test that mocked a
# plain JSON body here is what let the client's "read until it parses as
# JSON" loop ship and hang until its read timeout on every real capture.
_SSE_CREATE = (
    "event: interaction.created\r\n"
    'data: {"interaction": {"id": "i-42", "status": "in_progress",'
    ' "object": "interaction"}, "event_type": "interaction.created"}\r\n'
    "\r\n"
    "event: interaction.status_update\r\n"
    'data: {"interaction_id": "i-42", "status": "in_progress",'
    ' "event_type": "interaction.status_update"}\r\n'
    "\r\n"
    "event: step.start\r\n"
    'data: {"index": 0, "step": {"type": "thought"},'
    ' "event_type": "step.start"}\r\n'
    "\r\n"
)


def test_create_interaction_sends_stream_true(monkeypatch):
    """stream must literally be true on create (proved live: false hard-fails)."""
    seen = _mock_create(
        monkeypatch,
        lambda: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_SSE_CREATE.encode(),
        ),
    )

    assert research._create_interaction("dig into pinning") == "i-42"
    assert seen["url"].endswith("/locations/global/interactions")
    assert seen["json"]["stream"] is True
    assert seen["json"]["background"] is True
    assert seen["json"]["agent"] == research.DEEP_RESEARCH_AGENT
    assert seen["json"]["input"] == "dig into pinning"


def test_create_interaction_does_not_wait_out_the_stream(monkeypatch):
    """The id comes off the first event; the rest of the run is not our wait.

    The stream lives as long as the research does, so reading past the first
    event would block until the read timeout — the bug this test pins.
    """

    def body():
        yield _SSE_CREATE.encode()
        # Standing in for the rest of the run: reaching here means the
        # client kept reading instead of stopping at the id.
        raise AssertionError("read past the interaction id")

    _mock_create(monkeypatch, lambda: httpx.Response(200, content=body()))
    assert research._create_interaction("dig in") == "i-42"


def test_create_interaction_accepts_a_plain_json_body(monkeypatch):
    """Fallback: a non-SSE body still yields an id rather than raising."""
    _mock_create(
        monkeypatch,
        lambda: httpx.Response(200, json={"id": "i-7", "status": "in_progress"}),
    )
    assert research._create_interaction("dig in") == "i-7"


def test_create_interaction_without_an_id_raises(monkeypatch):
    _mock_create(monkeypatch, lambda: httpx.Response(200, json={"status": "weird"}))
    with pytest.raises(ValueError, match="no id"):
        research._create_interaction("dig in")


def test_create_interaction_raises_on_http_error(monkeypatch):
    """A streaming response must be read before raise_for_status can report it."""
    _mock_create(monkeypatch, lambda: httpx.Response(403, json={"error": "denied"}))
    with pytest.raises(httpx.HTTPStatusError):
        research._create_interaction("dig in")


# --- start_research_operation ----------------------------------------------


def test_start_research_writes_operation_and_enqueues(fs, enqueued, monkeypatch):
    note = _make_note(tags=["research", "rust"])
    prompts: list[str] = []

    def create(prompt: str) -> str:
        prompts.append(prompt)
        return "i-42"

    monkeypatch.setattr(research, "_create_interaction", create)

    out = research.start_research_operation(note.id)

    op = store.get(Operation, out["operation_id"])
    assert op is not None
    assert op.kind == "deep_research" and op.status == "running"
    assert op.interaction_id == "i-42"
    assert op.source_note_id == note.id
    assert enqueued == [op.id]
    # The note is data, not instructions — the prompt must say so and carry
    # the note's content.
    assert note.body in prompts[0]
    assert "not instructions" in prompts[0]


def test_start_research_unknown_note_errors_without_operation(fs, enqueued):
    out = research.start_research_operation(new_ulid())
    assert "not found" in out["error"]
    assert store.list_operations() == []


def test_start_research_create_failure_returns_error(fs, enqueued, monkeypatch):
    note = _make_note()

    def create(prompt: str) -> str:
        raise httpx.ConnectError("aiplatform down")

    monkeypatch.setattr(research, "_create_interaction", create)

    out = research.start_research_operation(note.id)
    assert "error" in out
    assert store.list_operations() == []
    assert enqueued == []


def test_start_research_enqueue_failure_fails_operation(fs, monkeypatch):
    note = _make_note()
    monkeypatch.setattr(research, "_create_interaction", lambda prompt: "i-42")

    def boom(operation_id: str, delay_seconds: int = 30) -> None:
        raise RuntimeError("no queue")

    monkeypatch.setattr(research, "_enqueue_poll", boom)

    out = research.start_research_operation(note.id)
    assert "enqueue failed" in out["error"]
    [op] = store.list_operations()
    assert op.status == "failed" and "no queue" in (op.error or "")


def test_enqueue_poll_inline_mode_skips_cloud_tasks(monkeypatch):
    monkeypatch.setenv("MEMEX_INLINE_POLL", "1")
    # Would raise on credentials/queue lookup if it touched Cloud Tasks.
    research._enqueue_poll("op-1")


# --- poll_operation transitions --------------------------------------------


def test_poll_in_progress_counts_attempt_and_reenqueues(fs, enqueued, monkeypatch):
    op = _make_operation()
    monkeypatch.setattr(
        research, "_get_interaction", lambda iid: {"status": "in_progress"}
    )

    out = research.poll_operation(op.id)

    assert out == {"operation_id": op.id, "status": "running", "attempts": 1}
    updated = store.get(Operation, op.id)
    assert updated is not None
    assert updated.status == "running" and updated.attempts == 1
    assert enqueued == [op.id]


def test_poll_attempt_cap_marks_failed(fs, enqueued, monkeypatch):
    op = _make_operation(attempts=research.MAX_ATTEMPTS - 1)
    monkeypatch.setattr(
        research, "_get_interaction", lambda iid: {"status": "in_progress"}
    )

    out = research.poll_operation(op.id)

    assert out["status"] == "failed"
    updated = store.get(Operation, op.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.attempts == research.MAX_ATTEMPTS
    assert "gave up" in (updated.error or "")
    assert enqueued == []


def test_poll_completed_writes_research_note(fs, enqueued, monkeypatch):
    source = _make_note(tags=["research", "rust"])
    op = _make_operation(source_note_id=source.id)
    # Real completed shape (proved live 2026-08-28): the report is chunked
    # across several type="model_output" steps, interleaved with "thought"
    # steps and inline-image parts that carry no text.
    interaction = {
        "status": "completed",
        "steps": [
            {"type": "user_input", "content": [{"text": "look into rust pinning"}]},
            {"type": "thought", "content": [{"text": "Planning the research"}]},
            {
                "type": "model_output",
                "content": [
                    {"type": "image", "data": "...", "mime_type": "image/png"},
                    {"type": "text", "text": "# Pinning report"},
                ],
            },
            {"type": "thought", "content": [{"text": "Refining"}]},
            {
                "type": "model_output",
                "content": [{"type": "text", "text": "Cited findings."}],
            },
        ],
    }
    monkeypatch.setattr(research, "_get_interaction", lambda iid: interaction)

    out = research.poll_operation(op.id)

    assert out["status"] == "completed"
    note = store.get(Note, out["result_note_id"])
    assert note is not None
    assert note.kind == "research"
    assert note.source_note_id == source.id
    # Report = every model_output step's text, concatenated — not just the
    # last step, which is only the final chunk.
    assert note.body == "# Pinning report\n\nCited findings."
    assert "research-report" in note.tags and "rust" in note.tags
    assert "research" not in note.tags
    assert [e.text for e in note.trace] == [
        "look into rust pinning",
        "Planning the research",
        "# Pinning report",
        "Refining",
        "Cited findings.",
    ]
    updated = store.get(Operation, op.id)
    assert updated is not None
    assert updated.status == "completed" and updated.result_note_id == note.id
    assert enqueued == []


def test_poll_completed_traces_thought_summaries(fs, enqueued, monkeypatch):
    """A live `thought` step carries its text under `summary`, not `content`.

    Reading only `content` left the report's trace with the prompt and the
    report and none of the reasoning between them.
    """
    source = _make_note(tags=["research"])
    op = _make_operation(source_note_id=source.id)
    interaction = {
        "status": "completed",
        "steps": [
            {"type": "user_input", "content": [{"text": "look into rust pinning"}]},
            {
                "type": "thought",
                "signature": "AY89a1+opaque",
                "summary": [{"text": "Planning the research", "type": "text"}],
            },
            {
                "type": "model_output",
                "content": [{"type": "text", "text": "# Pinning report"}],
            },
        ],
    }
    monkeypatch.setattr(research, "_get_interaction", lambda iid: interaction)

    out = research.poll_operation(op.id)

    note = store.get(Note, out["result_note_id"])
    assert note is not None
    assert [e.text for e in note.trace] == [
        "look into rust pinning",
        "Planning the research",
        "# Pinning report",
    ]
    # The report itself still comes only from the model_output steps.
    assert note.body == "# Pinning report"


def test_poll_failed_interaction_marks_failed_with_error(fs, enqueued, monkeypatch):
    op = _make_operation()
    interaction = {"status": "failed", "errors": [{"message": "quota exceeded"}]}
    monkeypatch.setattr(research, "_get_interaction", lambda iid: interaction)

    out = research.poll_operation(op.id)

    assert out["status"] == "failed"
    updated = store.get(Operation, op.id)
    assert updated is not None
    assert updated.status == "failed" and updated.error == "quota exceeded"
    assert enqueued == []


def test_poll_transient_get_failure_rides_the_reenqueue_loop(fs, enqueued, monkeypatch):
    op = _make_operation()

    def get(iid: str) -> dict:
        raise httpx.ConnectError("aiplatform down")

    monkeypatch.setattr(research, "_get_interaction", get)

    out = research.poll_operation(op.id)

    assert out["status"] == "running" and out["attempts"] == 1
    assert enqueued == [op.id]


def test_poll_settled_operation_is_idempotent(fs, enqueued, monkeypatch):
    op = _make_operation(status="completed")

    def get(iid: str) -> dict:
        raise AssertionError("settled operation must not be polled")

    monkeypatch.setattr(research, "_get_interaction", get)

    out = research.poll_operation(op.id)
    assert out["status"] == "completed" and out["deduped"]
    assert enqueued == []


def test_poll_unknown_operation_errors(fs):
    out = research.poll_operation(new_ulid())
    assert "not found" in out["error"]


# --- POST /internal/operations/poll ----------------------------------------


def test_internal_poll_endpoint(client, enqueued, monkeypatch):
    op = _make_operation()
    monkeypatch.setattr(
        research, "_get_interaction", lambda iid: {"status": "in_progress"}
    )
    r = client.post("/internal/operations/poll", json={"operation_id": op.id})
    assert r.status_code == 200
    assert r.json()["status"] == "running"
    assert enqueued == [op.id]


def test_internal_poll_unknown_operation_404(client):
    r = client.post("/internal/operations/poll", json={"operation_id": new_ulid()})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_internal_poll_bad_body_400(client):
    r = client.post("/internal/operations/poll", json={"nope": True})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


# --- enrichment trigger -----------------------------------------------------


def _canned(tags: list[str]) -> EnrichmentResult:
    return EnrichmentResult(
        transcript="look into rust pinning",
        summary="Look into rust pinning.",
        tags=tags,
        action_items=[ActionItem(title="Read the pinning chapter")],
    )


def _enrich_text_capture(monkeypatch, tags: list[str], starter) -> dict:
    """Run enrich_capture over a text capture with a canned enrichment."""
    import memex.agent.research as research_mod
    from memex.agent import service
    from memex.models import Capture

    cap = Capture(
        id=new_ulid(),
        created_at=store.now(),
        source="api",
        device_id="dev",
        kind="text",
        text="look into rust pinning",
        status="pending",
    )
    store.put(cap)
    monkeypatch.setattr(service, "enrich_text", lambda text: _canned(tags))
    monkeypatch.setattr(research_mod, "start_research_operation", starter)
    return service.enrich_capture(cap.id)


def test_enrichment_starts_research_on_tag(fs, monkeypatch):
    started: list[str] = []

    def starter(note_id: str) -> dict:
        started.append(note_id)
        return {"operation_id": new_ulid()}

    out = _enrich_text_capture(monkeypatch, ["research", "rust"], starter)

    assert "error" not in out
    assert out["capture"]["status"] == "enriched"
    assert started == [out["note"]["id"]]


def test_enrichment_without_tag_starts_nothing(fs, monkeypatch):
    started: list[str] = []

    def starter(note_id: str) -> dict:
        started.append(note_id)
        return {"operation_id": new_ulid()}

    out = _enrich_text_capture(monkeypatch, ["rust"], starter)

    assert "error" not in out
    assert started == []


def test_research_start_failure_never_fails_the_capture(fs, monkeypatch):
    def starter(note_id: str) -> dict:
        raise RuntimeError("cloud tasks exploded")

    out = _enrich_text_capture(monkeypatch, ["research"], starter)

    assert "error" not in out
    assert out["capture"]["status"] == "enriched"
    assert out["note"]["id"]
    # ...but it is not silent: the failure rides back in the result, or the
    # user cannot tell it from a note that never asked for research.
    assert "cloud tasks exploded" in out["research"]["error"]


def test_enrichment_result_carries_the_operation_id(fs, monkeypatch):
    def starter(note_id: str) -> dict:
        return {"operation_id": "op-1"}

    out = _enrich_text_capture(monkeypatch, ["research"], starter)
    assert out["research"] == {"operation_id": "op-1"}


def test_untagged_capture_carries_no_research_key(fs, monkeypatch):
    out = _enrich_text_capture(monkeypatch, ["rust"], lambda note_id: {})
    assert "research" not in out


def test_capture_response_surfaces_the_operation_id(client, fs, monkeypatch):
    """The sync capture endpoint hands the caller the operation it started."""
    from memex.agent import service

    monkeypatch.setattr(
        service, "enrich_text", lambda text: _canned(["research", "rust"])
    )
    monkeypatch.setattr(
        research, "start_research_operation", lambda note_id: {"operation_id": "op-9"}
    )

    r = client.post(
        "/api/v1/capture",
        json={"text": "research rust pinning"},
        headers=AUTH,
    )

    assert r.status_code == 201
    assert r.json()["research"] == {"operation_id": "op-9"}


# --- only the user's own words may start a paid run -------------------------


def _enrich_link_capture(monkeypatch, note: str | None, starter) -> dict:
    from memex.agent import service
    from memex.models import Capture

    cap = Capture(
        id=new_ulid(),
        created_at=store.now(),
        source="api",
        device_id="dev",
        kind="link",
        url="https://example.com/research-this-now",
        title="Research this: rust pinning",
        text=note,
        status="pending",
    )
    store.put(cap)
    monkeypatch.setattr(
        service, "enrich_link", lambda url, title, n: _canned(["research", "rust"])
    )
    monkeypatch.setattr(research, "start_research_operation", starter)
    return service.enrich_capture(cap.id)


def test_bare_link_never_starts_research(fs, monkeypatch):
    """A URL and a title are text a website chose — they cannot spend money."""

    def starter(note_id: str) -> dict:
        raise AssertionError("a bare link must not start a paid research run")

    out = _enrich_link_capture(monkeypatch, None, starter)

    assert "error" not in out
    assert "research" in out["note"]["tags"]  # the tag is there; the run is not
    assert "research" not in out


def test_link_with_a_user_note_may_start_research(fs, monkeypatch):
    started: list[str] = []

    def starter(note_id: str) -> dict:
        started.append(note_id)
        return {"operation_id": "op-2"}

    out = _enrich_link_capture(monkeypatch, "research this properly", starter)

    assert started == [out["note"]["id"]]
    assert out["research"] == {"operation_id": "op-2"}


# --- inline poll sweep -------------------------------------------------------


def test_poll_running_operations_sweeps_and_survives_failures(fs, monkeypatch):
    ok = _make_operation()
    bad = _make_operation()
    done = _make_operation(status="completed")
    polled: list[str] = []

    def fake_poll(operation_id: str) -> dict:
        polled.append(operation_id)
        if operation_id == bad.id:
            raise RuntimeError("boom")
        return {"operation_id": operation_id, "status": "running"}

    monkeypatch.setattr(research, "poll_operation", fake_poll)

    results = research.poll_running_operations()

    # Both running ops were polled, the settled one was not, and the failing
    # poll neither stopped the sweep nor leaked into the results.
    assert set(polled) == {ok.id, bad.id}
    assert done.id not in polled
    assert results == [{"operation_id": ok.id, "status": "running"}]
