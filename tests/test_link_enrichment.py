"""Link-capture enrichment against the store fake (no emulator, no Vertex).

The emulator-only suite in test_agent_service.py covers the full seam; these
run everywhere and pin the two things the read-later contract promises: a body
whose first line is a clickable markdown link, and the read-later tag.
"""

import pytest

from memex.ids import new_ulid
from memex.models import ActionItem, Capture, EnrichmentResult, Note
from memex.store import firestore as store


def _link_capture(**overrides) -> Capture:
    fields = {
        "id": new_ulid(),
        "created_at": store.now(),
        "source": "api",
        "device_id": "dev",
        "kind": "link",
        "url": "https://example.com/post",
        "title": "A post",
        "status": "pending",
        **overrides,
    }
    capture = Capture(**fields)
    store.put(capture)
    return capture


@pytest.fixture
def canned(monkeypatch):
    from memex.agent import service

    result = EnrichmentResult(
        transcript="Looks like a blog post.",
        summary="Worth a read this week.",
        tags=["blogging"],
        action_items=[ActionItem(title="Reply to the author")],
    )
    monkeypatch.setattr(service, "enrich_link", lambda url, title, note: result)
    return result


def test_body_leads_with_a_markdown_link(fs, canned):
    from memex.agent import service

    cap = _link_capture(text="skim the middle section")
    out = service.enrich_capture(cap.id)

    assert out["note"]["body"] == (
        "[A post](https://example.com/post)\n\nskim the middle section"
    )
    assert out["note"]["kind"] == "link"
    assert out["note"]["summary"] == "Worth a read this week."


def test_body_falls_back_to_the_url_when_untitled(fs, canned):
    from memex.agent import service

    cap = _link_capture(title=None)
    out = service.enrich_capture(cap.id)

    assert out["note"]["body"] == "[https://example.com/post](https://example.com/post)"


def test_markdown_in_a_title_cannot_break_the_link(fs, canned):
    from memex.agent import service

    cap = _link_capture(title="Rust [async] *book* <a href=x>")
    out = service.enrich_capture(cap.id)

    assert out["note"]["body"] == (
        r"[Rust \[async\] \*book\* \<a href=x\>](https://example.com/post)"
    )


def test_parens_in_a_url_cannot_end_the_link_early(fs, canned):
    from memex.agent import service

    cap = _link_capture(url="https://en.wikipedia.org/wiki/Foo_(bar)")
    out = service.enrich_capture(cap.id)

    assert out["note"]["body"] == (
        "[A post](https://en.wikipedia.org/wiki/Foo_%28bar%29)"
    )


def test_a_newline_in_a_title_cannot_break_the_body(fs, canned):
    from memex.agent import service

    cap = _link_capture(title="Buy now\n\n# Free money")
    out = service.enrich_capture(cap.id)

    body = out["note"]["body"]
    assert "\n" not in body
    assert body == "[Buy now  # Free money](https://example.com/post)"


def test_read_later_tag_is_always_present(fs, canned):
    from memex.agent import service

    cap = _link_capture()
    out = service.enrich_capture(cap.id)

    assert out["note"]["tags"] == ["read-later", "blogging"]


def test_read_later_tag_is_not_duplicated(fs, monkeypatch):
    from memex.agent import service

    monkeypatch.setattr(
        service,
        "enrich_link",
        lambda url, title, note: EnrichmentResult(
            transcript="t", summary="s", tags=["read-later"], action_items=[]
        ),
    )
    cap = _link_capture()
    out = service.enrich_capture(cap.id)

    assert out["note"]["tags"] == ["read-later"]


def test_action_items_from_the_users_note_still_become_tasks(fs, canned):
    from memex.agent import service

    cap = _link_capture(text="reply to the author")
    out = service.enrich_capture(cap.id)

    assert [t["title"] for t in out["tasks"]] == ["Reply to the author"]
    note = store.get(Note, out["note"]["id"])
    assert note is not None and note.task_ids == [out["tasks"][0]["id"]]


def test_a_bare_link_produces_no_tasks(fs, canned):
    """A URL and the title the site chose are not the user asking for
    anything. Without a note of their own, an action item could only have
    come from the page, so none is kept."""
    cap = _link_capture()  # no user note
    from memex.agent import service

    out = service.enrich_capture(cap.id)

    assert out["tasks"] == []
    note = store.get(Note, out["note"]["id"])
    assert note is not None and note.task_ids == []


def test_link_trace_records_the_note_a_task_came_from(fs, canned):
    from memex.agent import service

    cap = _link_capture(title="A post", text="reply to the author")
    out = service.enrich_capture(cap.id)

    note = store.get(Note, out["note"]["id"])
    assert note is not None
    assert note.trace[0].args == {
        "url": "https://example.com/post",
        "title": "A post",
        "note": "reply to the author",
    }


def test_link_capture_without_a_url_fails_cleanly(fs, canned):
    from memex.agent import service

    cap = _link_capture()
    store.update(Capture, cap.id, {"url": None})

    out = service.enrich_capture(cap.id)

    assert out["note"] is None
    assert "no url" in out["error"]
    stored = store.get(Capture, cap.id)
    assert stored is not None and stored.status == "failed"


def test_generated_tags_are_normalized_and_filterable(fs, monkeypatch):
    """Tags off the model land in filter URLs like any other, so they go
    through the same normalization the contract promises."""
    from memex.agent import service

    monkeypatch.setattr(
        service,
        "enrich_link",
        lambda url, title, note: EnrichmentResult(
            transcript="t",
            summary="s",
            tags=["Read Later", "foo,bar", "Read Later"],
            action_items=[],
        ),
    )
    cap = _link_capture()
    out = service.enrich_capture(cap.id)

    assert out["note"]["tags"] == ["read-later", "foo", "bar"]
