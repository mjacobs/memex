"""The image branch of enrich_capture, against the in-memory store fake.

Vertex and GCS are both stubbed: this pins the wiring (download, prompt
inputs, note shape), not the model's words.
"""

from memex.ids import new_ulid
from memex.models import ActionItem, Capture, EnrichmentResult, Note
from memex.store import firestore as store

IMAGE = b"\x89PNG\r\n\x1a\n-bytes"

CANNED = EnrichmentResult(
    transcript="A pricing table listing Hobby $0 and Pro $20 per month.",
    summary="Pricing table for the Pro plan at $20/mo.",
    tags=["pricing"],
    action_items=[ActionItem(title="Compare against current plan")],
)


def _image_capture(**overrides) -> Capture:
    fields = {
        "id": new_ulid(),
        "created_at": store.now(),
        "source": "web",
        "device_id": "dev",
        "kind": "image",
        "image_gcs_uri": "gs://test-bucket/captures/x.png",
        "image_mime": "image/png",
        "status": "pending",
    }
    fields.update(overrides)
    cap = Capture(**fields)
    store.put(cap)
    return cap


def test_enrich_image_writes_note_with_caption_and_source(fs, monkeypatch):
    from memex.agent import service

    cap = _image_capture(
        text="compare later",
        source_url="https://example.com/pricing",
        title="Example — Pricing",
    )
    seen: dict = {}

    def fake_enrich_image(image, mime, *, caption, source_url, title):
        seen.update(
            image=image, mime=mime, caption=caption, source_url=source_url, title=title
        )
        return CANNED

    monkeypatch.setattr(service, "_download_gcs", lambda uri: IMAGE)
    monkeypatch.setattr(service, "enrich_image", fake_enrich_image)

    out = service.enrich_capture(cap.id)

    assert "error" not in out
    assert seen == {
        "image": IMAGE,
        "mime": "image/png",
        "caption": "compare later",
        "source_url": "https://example.com/pricing",
        "title": "Example — Pricing",
    }
    assert out["capture"]["status"] == "enriched"
    note = store.get(Note, out["note"]["id"])
    assert note is not None
    # Description, the user's own caption, then a linked source.
    assert note.body == (
        "A pricing table listing Hobby $0 and Pro $20 per month.\n\n"
        "**Note:** compare later\n\n"
        "Source: [Example — Pricing](https://example.com/pricing)"
    )
    assert note.transcript is None  # transcript is an audio-only field
    assert note.summary == CANNED.summary
    assert note.tags == ["pricing"]
    assert len(note.task_ids) == 1
    assert len(note.trace) == 2  # image input + model result
    assert "image capture" in (note.trace[0].text or "")


def test_enrich_image_without_metadata_is_just_the_description(fs, monkeypatch):
    from memex.agent import service

    cap = _image_capture()
    monkeypatch.setattr(service, "_download_gcs", lambda uri: IMAGE)
    monkeypatch.setattr(service, "enrich_image", lambda *a, **k: CANNED)

    out = service.enrich_capture(cap.id)

    assert out["note"]["body"] == CANNED.transcript


def test_code_in_a_screenshot_survives_into_the_body(fs, monkeypatch):
    """A screenshot of code is the common case, and the body is markdown the
    app composes — "List<T>" must reach the page, not the sanitizer's floor."""
    from memex.agent import service
    from memex.models import EnrichmentResult

    described = EnrichmentResult(
        transcript="The signature reads List<T> map(*args) _slowly_.",
        summary="A generic signature.",
        tags=["code"],
        action_items=[],
    )
    cap = _image_capture()
    monkeypatch.setattr(service, "_download_gcs", lambda uri: IMAGE)
    monkeypatch.setattr(service, "enrich_image", lambda *a, **k: described)

    out = service.enrich_capture(cap.id)

    assert out["note"]["body"] == (
        r"The signature reads List\<T\> map(\*args) \_slowly\_."
    )


def test_enrich_image_without_uri_fails_capture(fs, monkeypatch):
    from memex.agent import service

    cap = _image_capture(image_gcs_uri=None)
    out = service.enrich_capture(cap.id)

    assert out["error"] == "image capture has no image_gcs_uri"
    assert out["note"] is None
    stored = store.get(Capture, cap.id)
    assert stored is not None and stored.status == "failed"
