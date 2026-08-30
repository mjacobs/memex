"""Deep-research lifecycle: the request flag, poll transitions, internal route.

The aiplatform HTTP calls and Cloud Tasks enqueues are mocked — contract
tests assert the operation/notes plumbing, not Deep Research output.
"""

import json

import httpx
import pytest

from memex.agent import research
from memex.ids import new_ulid
from memex.models import ActionItem, EnrichmentResult, Note, Operation, TraceEvent
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


def test_poll_replays_onto_the_reserved_report_note(fs, enqueued, monkeypatch):
    """A retry after a crash mid-completion must not add a second report.

    The operation carries the report note's id from before the crash, so the
    replayed poll rewrites that same document instead of writing a new one.
    """
    source = _make_note(tags=["research"])
    reserved = new_ulid()
    op = _make_operation(source_note_id=source.id, result_note_id=reserved)
    monkeypatch.setattr(
        research,
        "_get_interaction",
        lambda iid: {
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{"text": "report"}]}],
        },
    )

    out = research.poll_operation(op.id)

    assert out["result_note_id"] == reserved
    reports = [n for n in store.query(Note, limit=50) if n.kind == "research"]
    assert [n.id for n in reports] == [reserved]

    # And the delivery that lost the race sees the settled operation, not a
    # second run of the completion path.
    again = research.poll_operation(op.id)
    assert again["status"] == "completed" and again["deduped"]
    reports = [n for n in store.query(Note, limit=50) if n.kind == "research"]
    assert [n.id for n in reports] == [reserved]


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


def _enrich_text_capture(
    monkeypatch, tags: list[str], starter, *, research_requested: bool = False
) -> dict:
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
        research=research_requested,
        status="pending",
    )
    store.put(cap)
    monkeypatch.setattr(service, "enrich_text", lambda text: _canned(tags))
    monkeypatch.setattr(research_mod, "start_research_operation", starter)
    return service.enrich_capture(cap.id)


def test_enrichment_starts_research_when_the_capture_asked(fs, monkeypatch):
    started: list[str] = []

    def starter(note_id: str, merge_into_source: bool = False) -> dict:
        started.append(note_id)
        return {"operation_id": new_ulid()}

    out = _enrich_text_capture(
        monkeypatch, ["rust"], starter, research_requested=True
    )

    assert "error" not in out
    assert out["capture"]["status"] == "enriched"
    assert started == [out["note"]["id"]]


def test_a_research_tag_alone_starts_nothing(fs, monkeypatch):
    """The model's tags classify a note; they do not authorize spending.

    Enrichment tags are the model's reading of content that may have come
    from a web page, so `research` in them is a topic label and nothing more.
    """

    def starter(note_id: str, merge_into_source: bool = False) -> dict:
        raise AssertionError("a model-emitted tag must not start a paid run")

    out = _enrich_text_capture(monkeypatch, ["research", "rust"], starter)

    assert "error" not in out
    assert "research" in out["note"]["tags"]  # the tag is there; the run is not
    assert "research" not in out


def test_research_start_failure_never_fails_the_capture(fs, monkeypatch):
    def starter(note_id: str, merge_into_source: bool = False) -> dict:
        raise RuntimeError("cloud tasks exploded")

    out = _enrich_text_capture(
        monkeypatch, ["rust"], starter, research_requested=True
    )

    assert "error" not in out
    assert out["capture"]["status"] == "enriched"
    assert out["note"]["id"]
    # ...but it is not silent: the failure rides back in the result, or the
    # user cannot tell it from a note that never asked for research.
    assert "cloud tasks exploded" in out["research"]["error"]


def test_enrichment_result_carries_the_operation_id(fs, monkeypatch):
    def starter(note_id: str, merge_into_source: bool = False) -> dict:
        return {"operation_id": "op-1"}

    out = _enrich_text_capture(
        monkeypatch, ["rust"], starter, research_requested=True
    )
    assert out["research"] == {"operation_id": "op-1"}


def test_capture_that_did_not_ask_carries_no_research_key(fs, monkeypatch):
    out = _enrich_text_capture(monkeypatch, ["rust"], lambda note_id: {})
    assert "research" not in out


def test_capture_response_surfaces_the_operation_id(client, fs, monkeypatch):
    """The sync capture endpoint hands the caller the operation it started."""
    from memex.agent import service

    monkeypatch.setattr(service, "enrich_text", lambda text: _canned(["rust"]))
    monkeypatch.setattr(
        research, "start_research_operation", lambda note_id, merge_into_source=False: {"operation_id": "op-9"}
    )

    r = client.post(
        "/api/v1/capture",
        json={"text": "rust pinning", "research": True},
        headers=AUTH,
    )

    assert r.status_code == 201
    assert r.json()["capture"]["research"] is True
    assert r.json()["research"] == {"operation_id": "op-9"}


# --- only an explicit request may start a paid run --------------------------


def _enrich_link_capture(
    monkeypatch, note: str | None, starter, *, research_requested: bool = False
) -> dict:
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
        research=research_requested,
        status="pending",
    )
    store.put(cap)
    monkeypatch.setattr(
        service, "enrich_link", lambda url, title, n: _canned(["research", "rust"])
    )
    monkeypatch.setattr(research, "start_research_operation", starter)
    return service.enrich_capture(cap.id)


def test_a_page_that_says_research_this_cannot_spend_money(fs, monkeypatch):
    """The page's title, and the model's tags off it, are not authorization.

    A caption doesn't change that: "save this" alongside a page titled
    "Research this" used to be enough to start a billed run.
    """

    def starter(note_id: str, merge_into_source: bool = False) -> dict:
        raise AssertionError("page text must not start a paid research run")

    for caption in (None, "save this"):
        out = _enrich_link_capture(monkeypatch, caption, starter)
        assert "error" not in out
        assert "research" in out["note"]["tags"]  # the tag is there; the run is not
        assert "research" not in out


def test_link_that_asked_for_research_starts_it(fs, monkeypatch):
    started: list[str] = []

    def starter(note_id: str, merge_into_source: bool = False) -> dict:
        started.append(note_id)
        return {"operation_id": "op-2"}

    out = _enrich_link_capture(
        monkeypatch, "worth a dig", starter, research_requested=True
    )

    assert started == [out["note"]["id"]]
    assert out["research"] == {"operation_id": "op-2"}


def test_capture_endpoints_carry_the_research_flag(client, fs, fake_gcs, monkeypatch):
    """Every capture kind can ask, and the flag reaches the stored capture."""
    from memex.agent import service
    from memex.models import Capture

    monkeypatch.setattr(service, "enrich_link", lambda url, title, n: _canned(["rust"]))
    monkeypatch.setattr(
        research, "start_research_operation", lambda note_id, merge_into_source=False: {"operation_id": "op-3"}
    )

    r = client.post(
        "/api/v1/capture/link",
        json={"url": "https://example.com/pinning", "research": True},
        headers=AUTH,
    )
    assert r.status_code == 201
    assert r.json()["capture"]["research"] is True
    assert r.json()["research"] == {"operation_id": "op-3"}

    # Audio arrives as a raw body, so its flag rides a header.
    r = client.post(
        "/api/v1/capture/audio",
        content=b"fake wav bytes",
        headers={**AUTH, "Content-Type": "audio/wav", "X-Memex-Research": "1"},
    )
    assert r.status_code == 202
    stored = store.get(Capture, r.json()["id"])
    assert stored is not None and stored.research is True


def test_link_endpoints_surface_the_operation_they_started(client, fs, monkeypatch):
    """A link that asked for research learns which run it got.

    Both link routes used to run enrichment and throw its result away, so a
    caller got the capture back with no operation id and no kickoff error —
    a failed run was indistinguishable from one that was never requested.
    """
    from memex.agent import service

    monkeypatch.setattr(service, "enrich_link", lambda url, title, n: _canned(["rust"]))
    monkeypatch.setattr(
        research, "start_research_operation", lambda note_id, merge_into_source=False: {"error": "no quota"}
    )

    r = client.post(
        "/api/v1/capture/links",
        json={
            "links": [
                {"url": "https://example.com/a", "research": True},
                {"url": "https://example.com/b"},
            ]
        },
        headers=AUTH,
    )

    assert r.status_code == 201
    asked, did_not = r.json()["results"]
    assert asked["research"] == {"error": "no quota"}
    assert "research" not in did_not


def test_capture_research_flag_defaults_off(client, fs, monkeypatch):
    """A client that never sends the flag never starts a paid run."""
    from memex.agent import service

    monkeypatch.setattr(service, "enrich_text", lambda text: _canned(["research"]))

    def starter(note_id: str, merge_into_source: bool = False) -> dict:
        raise AssertionError("an omitted flag must not start a paid research run")

    monkeypatch.setattr(research, "start_research_operation", starter)

    r = client.post("/api/v1/capture", json={"text": "rust pinning"}, headers=AUTH)

    assert r.status_code == 201
    assert r.json()["capture"]["research"] is False
    assert "research" not in r.json()


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


# --- capture-time merge: one note is both the question and the report -------


def _report_interaction(text: str = "# Report\n\nFindings.") -> dict:
    return {
        "status": "completed",
        "steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}],
    }


def test_merge_rewrites_the_asking_note_instead_of_adding_one(fs, enqueued, monkeypatch):
    """The capture-time path: the note that asked becomes the report.

    A capture written only to pose a question, plus a report note repeating
    it, is the same thing twice in the feed.
    """
    source = _make_note(tags=["rust"], body="how does rust pinning actually work")
    op = _make_operation(source_note_id=source.id, merge_into_source=True)
    monkeypatch.setattr(research, "_get_interaction", lambda iid: _report_interaction())

    before = len(store.query(Note, limit=100))
    out = research.poll_operation(op.id)

    assert out["status"] == "completed"
    assert out["result_note_id"] == source.id
    assert len(store.query(Note, limit=100)) == before, "no second note"

    note = store.get(Note, source.id)
    assert note is not None
    assert note.kind == "research"
    assert note.body == "# Report\n\nFindings."
    # The user's own words are the one thing the report must not eat.
    assert note.original_body == "how does rust pinning actually work"
    assert note.research_status == "completed"
    assert note.summary.startswith("Research report:")
    assert "research-report" in note.tags and "rust" in note.tags
    # It is its own source, so there is no other note to point at.
    assert note.source_note_id is None


def test_a_merge_replay_rebuilds_from_the_question_not_the_report(
    fs, enqueued, monkeypatch
):
    """Cloud Tasks delivers at least once, so completion can run twice.

    The second pass must re-read `original_body` as the source text; reading
    `body` would feed the previous report back in as the question.
    """
    source = _make_note(body="how does rust pinning actually work")
    op = _make_operation(source_note_id=source.id, merge_into_source=True)
    monkeypatch.setattr(research, "_get_interaction", lambda iid: _report_interaction())

    research.poll_operation(op.id)
    # A replay finds the operation already settled and leaves it alone, so
    # drive the merge directly to prove the rewrite itself is idempotent.
    research._merge_research_into_source(store.get(Operation, op.id), _report_interaction())

    note = store.get(Note, source.id)
    assert note is not None
    assert note.original_body == "how does rust pinning actually work"
    assert note.body == "# Report\n\nFindings."
    assert note.summary.count("Research report:") == 1
    assert note.tags.count("research-report") == 1


def test_a_failed_run_hands_back_what_the_user_wrote(fs, enqueued, monkeypatch):
    """The case that justifies dropping the capture note.

    With no second note behind it, a merged run that dies is the only thing
    standing between the user and a lost thought — so failure must leave the
    note exactly as they wrote it.
    """
    source = _make_note(body="how does rust pinning actually work")
    op = _make_operation(source_note_id=source.id, merge_into_source=True)
    monkeypatch.setattr(
        research,
        "_get_interaction",
        lambda iid: {"status": "failed", "errors": [{"message": "quota"}]},
    )

    out = research.poll_operation(op.id)

    assert out["status"] == "failed"
    note = store.get(Note, source.id)
    assert note is not None
    assert note.body == "how does rust pinning actually work"
    assert note.kind == "capture", "a note with no report is not a research note"
    assert note.original_body is None
    assert note.research_status == "failed"


def test_giving_up_after_the_poll_cap_also_frees_the_note(fs, enqueued, monkeypatch):
    source = _make_note()
    op = _make_operation(
        source_note_id=source.id,
        merge_into_source=True,
        attempts=research.MAX_ATTEMPTS - 1,
    )
    monkeypatch.setattr(
        research, "_get_interaction", lambda iid: {"status": "in_progress"}
    )

    out = research.poll_operation(op.id)

    assert out["status"] == "failed"
    note = store.get(Note, source.id)
    assert note is not None and note.research_status == "failed"


def test_research_on_an_existing_note_still_writes_a_second_note(
    fs, enqueued, monkeypatch
):
    """A note that already stands on its own keeps its own identity.

    Researching a link saved three weeks ago should not turn that link into a
    report; only the capture-time path merges.
    """
    source = _make_note(tags=["sqlite"], body="https://example.com/wal-mode")
    op = _make_operation(source_note_id=source.id)  # merge_into_source defaults False
    monkeypatch.setattr(research, "_get_interaction", lambda iid: _report_interaction())

    out = research.poll_operation(op.id)

    assert out["result_note_id"] != source.id
    report = store.get(Note, out["result_note_id"])
    assert report is not None and report.source_note_id == source.id
    kept = store.get(Note, source.id)
    assert kept is not None
    assert kept.kind == "capture" and kept.body == "https://example.com/wal-mode"
    # It is not the report, but it should stop saying one is coming.
    assert kept.research_status == "completed"


# --- POST /notes/{id}/research ---------------------------------------------


def test_note_research_route_starts_a_run(fs, client, monkeypatch):
    note = _make_note()
    calls: list[tuple[str, bool]] = []

    def starter(note_id: str, merge_into_source: bool = False) -> dict:
        calls.append((note_id, merge_into_source))
        store.update(Note, note_id, {"research_status": "running"})
        return {"operation_id": "op-77"}

    monkeypatch.setattr(research, "start_research_operation", starter)

    r = client.post(f"/api/v1/notes/{note.id}/research", headers=AUTH)

    assert r.status_code == 202
    assert r.json() == {"operation_id": "op-77", "status": "running"}
    # An existing note keeps its identity: this path never merges.
    assert calls == [(note.id, False)]


def test_note_research_route_refuses_a_second_concurrent_run(
    fs, client, enqueued, monkeypatch
):
    """409 without creating an interaction — the refusal has to come before
    the money, not after it."""
    note = _make_note()
    store.update(Note, note.id, {"research_status": "running"})
    monkeypatch.setattr(
        research,
        "_create_interaction",
        lambda prompt: pytest.fail("must not create a paid interaction"),
    )

    r = client.post(f"/api/v1/notes/{note.id}/research", headers=AUTH)

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "already_running"


def test_note_research_route_404s_for_an_unknown_note(fs, client):
    r = client.post(f"/api/v1/notes/{new_ulid()}/research", headers=AUTH)
    assert r.status_code == 404


def test_note_research_route_needs_the_device_key(fs, client):
    note = _make_note()
    assert client.post(f"/api/v1/notes/{note.id}/research").status_code == 401


def test_note_research_route_reports_a_kickoff_that_failed(fs, client, monkeypatch):
    note = _make_note()
    monkeypatch.setattr(
        research,
        "start_research_operation",
        lambda note_id, merge_into_source=False: {"error": "no quota"},
    )

    r = client.post(f"/api/v1/notes/{note.id}/research", headers=AUTH)

    assert r.status_code == 502
    assert r.json()["error"]["code"] == "research_failed"


# --- which capture kinds merge ---------------------------------------------


def _enrich_capture_of_kind(monkeypatch, kind: str, starter, **capture_fields) -> dict:
    """Run enrich_capture over a capture of `kind` with a canned enrichment."""
    import memex.agent.research as research_mod
    from memex.agent import service
    from memex.models import Capture

    cap = Capture(
        id=new_ulid(),
        created_at=store.now(),
        source="api",
        device_id="dev",
        kind=kind,
        research=True,
        status="pending",
        **capture_fields,
    )
    store.put(cap)
    monkeypatch.setattr(service, "enrich_text", lambda text: _canned(["rust"]))
    monkeypatch.setattr(service, "enrich_link", lambda *a: _canned(["sqlite"]))
    monkeypatch.setattr(research_mod, "start_research_operation", starter)
    return service.enrich_capture(cap.id)


def _recording_starter(calls: list[bool]):
    def starter(note_id: str, merge_into_source: bool = False) -> dict:
        calls.append(merge_into_source)
        return {"operation_id": new_ulid()}

    return starter


def test_a_typed_question_merges(fs, monkeypatch):
    calls: list[bool] = []
    _enrich_capture_of_kind(
        monkeypatch, "text", _recording_starter(calls), text="how does pinning work"
    )
    assert calls == [True]


def test_a_saved_link_does_not_merge(fs, monkeypatch):
    """Tabby stashes pages to read later.

    Consuming one into a report would take away the page you meant to keep,
    so a link that asked for research gets a report note beside it.
    """
    calls: list[bool] = []
    _enrich_capture_of_kind(
        monkeypatch,
        "link",
        _recording_starter(calls),
        url="https://example.com/wal-mode",
        title="WAL mode",
    )
    assert calls == [False]


def test_only_one_of_two_racing_kickoffs_creates_an_interaction(
    fs, enqueued, monkeypatch
):
    """A double tap must not buy two reports.

    The old shape read the note, saw "not running", and only then created the
    interaction — a window wide enough for a second request to do the same.
    The claim closes it: the loser stops before spending anything.
    """
    note = _make_note()
    created: list[str] = []
    monkeypatch.setattr(
        research,
        "_create_interaction",
        lambda prompt: created.append(prompt) or f"i-{len(created)}",
    )

    first = research.start_research_operation(note.id)
    second = research.start_research_operation(note.id)

    assert "operation_id" in first
    assert second.get("code") == "already_running"
    assert len(created) == 1, "the loser must not create a paid interaction"


def test_a_kickoff_that_dies_hands_the_note_back(fs, enqueued, monkeypatch):
    """A claim that never became a run must not leave the note busy forever,
    or it could never be researched again."""
    note = _make_note()
    monkeypatch.setattr(
        research,
        "_create_interaction",
        lambda prompt: (_ for _ in ()).throw(RuntimeError("no quota")),
    )

    out = research.start_research_operation(note.id)

    assert "error" in out
    refreshed = store.get(Note, note.id)
    assert refreshed is not None and refreshed.research_status is None
    # And the note can be researched again once the outage passes.
    assert store.claim_note_research(note.id) is True


def test_merging_keeps_the_notes_own_trace(fs, enqueued, monkeypatch):
    """The trace is the honesty surface: how the note became a note, plus a
    user event per owner edit. The report is appended to that history, not
    written over it."""
    source = _make_note()
    store.update(
        Note,
        source.id,
        {
            "trace": [
                TraceEvent(t=store.now(), role="user", text="Edited summary").model_dump(
                    mode="python"
                )
            ]
        },
    )
    op = _make_operation(source_note_id=source.id, merge_into_source=True)
    monkeypatch.setattr(research, "_get_interaction", lambda iid: _report_interaction())

    research.poll_operation(op.id)

    note = store.get(Note, source.id)
    assert note is not None
    texts = [e.text for e in note.trace]
    assert "Edited summary" in texts, "the note's own history survived"
    assert "# Report\n\nFindings." in texts, "the report's steps were added"


def test_two_completion_deliveries_write_one_report(fs, enqueued, monkeypatch):
    """Cloud Tasks delivers at least once, and both deliveries can read the
    operation as running: reserving the result has to be exclusive, or the
    split path writes two report notes."""
    source = _make_note()
    op = _make_operation(source_note_id=source.id)
    monkeypatch.setattr(research, "_get_interaction", lambda iid: _report_interaction())

    before = len(store.query(Note, limit=100))
    first = research.poll_operation(op.id)
    # A delivery that read the operation before the first one settled.
    second = research.poll_operation(op.id)

    assert first["status"] == "completed"
    assert second.get("deduped") is True
    assert len(store.query(Note, limit=100)) == before + 1


def test_a_failed_run_frees_the_note_even_if_settling_is_lost(
    fs, enqueued, monkeypatch
):
    """The note is freed before the operation settles, so a crash between the
    two cannot leave a settled run against a note that reads as busy — which
    nothing would ever repair, blocking research on it forever."""
    source = _make_note()
    op = _make_operation(source_note_id=source.id)
    store.update(Note, source.id, {"research_status": "running"})
    monkeypatch.setattr(
        research,
        "_get_interaction",
        lambda iid: {"status": "failed", "errors": [{"message": "quota"}]},
    )
    monkeypatch.setattr(
        store, "transition_operation", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash"))
    )

    with pytest.raises(RuntimeError):
        research.poll_operation(op.id)

    note = store.get(Note, source.id)
    assert note is not None and note.research_status == "failed"
    assert store.claim_note_research(source.id) is True


def test_search_finds_a_merged_report_by_the_question_it_answers(fs):
    from memex.agent import tools

    note = _make_note(body="are heat pump water heaters worth it in a mild climate")
    store.update(
        Note,
        note.id,
        {
            "kind": "research",
            "body": "# Report\n\nUnrelated prose about efficiency ratings.",
            "original_body": "are heat pump water heaters worth it in a mild climate",
            "research_status": "completed",
        },
    )

    hits = tools.search_notes("mild climate")["notes"]

    assert [n["id"] for n in hits] == [note.id]


def test_a_lost_operation_write_does_not_free_the_note_to_buy_another(
    fs, enqueued, monkeypatch
):
    """Once the interaction exists it is billing.

    Releasing the claim here would let a retry create a second one, so the
    note stays claimed: a note that reads as busy is a worse experience than
    a duplicate report is a cost.
    """
    note = _make_note()
    monkeypatch.setattr(research, "_create_interaction", lambda prompt: "i-paid")
    monkeypatch.setattr(
        store, "put", lambda entity: (_ for _ in ()).throw(RuntimeError("firestore"))
    )

    out = research.start_research_operation(note.id)

    assert "error" in out
    refreshed = store.get(Note, note.id)
    assert refreshed is not None and refreshed.research_status == "running"
    assert store.claim_note_research(note.id) is False


def test_a_merge_that_failed_after_reserving_can_resume(fs, enqueued, monkeypatch):
    """The reservation must not dedupe an operation against itself, or a
    merge that threw mid-flight leaves the run permanently running."""
    source = _make_note()
    op = _make_operation(source_note_id=source.id, merge_into_source=True)
    monkeypatch.setattr(research, "_get_interaction", lambda iid: _report_interaction())

    # First pass reserves, then dies inside the merge.
    real_merge = research._merge_research_into_source

    def boom(*args, **kwargs):
        raise RuntimeError("crash")

    monkeypatch.setattr(research, "_merge_research_into_source", boom)
    with pytest.raises(RuntimeError):
        research.poll_operation(op.id)
    assert store.get(Operation, op.id).result_note_id == source.id

    # The redelivery resumes rather than deduping against its own reservation.
    monkeypatch.setattr(research, "_merge_research_into_source", real_merge)
    out = research.poll_operation(op.id)

    assert out["status"] == "completed"
    assert store.get(Operation, op.id).status == "completed"


def test_a_transient_note_failure_does_not_settle_the_operation(
    fs, enqueued, monkeypatch
):
    """Swallowing this would settle the run against a note stuck reading as
    running, which nothing would ever repair."""
    source = _make_note()
    op = _make_operation(source_note_id=source.id)
    store.update(Note, source.id, {"research_status": "running"})
    monkeypatch.setattr(
        research,
        "_get_interaction",
        lambda iid: {"status": "failed", "errors": [{"message": "quota"}]},
    )
    monkeypatch.setattr(
        store, "update", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("transient"))
    )

    with pytest.raises(RuntimeError):
        research.poll_operation(op.id)

    # Still running, so the poll comes back rather than stranding the note.
    assert store.get(Operation, op.id).status == "running"


def test_a_deleted_note_does_not_wedge_its_operation(fs, enqueued, monkeypatch):
    """The other side of the same coin: gone is fine, and must settle."""
    source = _make_note()
    op = _make_operation(source_note_id=source.id)
    store.delete(Note, source.id)
    monkeypatch.setattr(research, "_get_interaction", lambda iid: _report_interaction())

    out = research.poll_operation(op.id)

    assert out["status"] == "completed"
    assert store.get(Operation, op.id).status == "completed"
