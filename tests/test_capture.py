"""Text, audio, and image capture paths."""

import base64

from tests.conftest import AUTH

PNG = b"\x89PNG\r\n\x1a\n-fake-image-bytes"


def _image_body(**overrides) -> dict:
    body = {"image_base64": base64.b64encode(PNG).decode(), "mime": "image/png"}
    body.update(overrides)
    return body


def test_text_capture_happy_path(client, agent_stub):
    r = client.post(
        "/api/v1/capture", json={"text": "buy milk", "source": "desktop"}, headers=AUTH
    )
    assert r.status_code == 201
    body = r.json()
    assert body["capture"]["kind"] == "text"
    assert body["capture"]["source"] == "desktop"
    assert body["capture"]["status"] == "enriched"
    assert body["note"]["body"] == "buy milk"
    assert body["note"]["capture_id"] == body["capture"]["id"]
    assert [t["id"] for t in body["tasks"]] == body["note"]["task_ids"]
    assert agent_stub["enrich"] == [body["capture"]["id"]]


def test_text_capture_unknown_source_falls_back_to_api(client, agent_stub):
    r = client.post(
        "/api/v1/capture", json={"text": "x", "source": "toaster"}, headers=AUTH
    )
    assert r.status_code == 201
    assert r.json()["capture"]["source"] == "api"


def test_text_capture_agent_unavailable_503(client, agent_missing):
    r = client.post("/api/v1/capture", json={"text": "x"}, headers=AUTH)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "agent_unavailable"


def test_audio_capture_202_and_gcs_write(client, fake_gcs):
    r = client.post(
        "/api/v1/capture/audio",
        content=b"fake-m4a-bytes",
        headers={**AUTH, "Content-Type": "audio/mp4", "X-Memex-Source": "ios"},
    )
    assert r.status_code == 202
    capture_id = r.json()["id"]
    assert fake_gcs == [
        {
            "capture_id": capture_id,
            "ext": "m4a",
            "data": b"fake-m4a-bytes",
            "content_type": "audio/mp4",
        }
    ]
    # pending capture doc exists and is pollable
    r2 = client.get(f"/api/v1/captures/{capture_id}", headers=AUTH)
    assert r2.status_code == 200
    cap = r2.json()["capture"]
    assert cap["kind"] == "audio"
    assert cap["status"] == "pending"
    assert cap["source"] == "ios"
    assert cap["audio_gcs_uri"] == f"gs://test-bucket/captures/{capture_id}.m4a"
    assert cap["audio_mime"] == "audio/mp4"


def test_audio_capture_ext_mapping(client, fake_gcs):
    for content_type, ext in [
        ("audio/x-m4a", "m4a"),
        ("audio/m4a", "m4a"),
        ("audio/wav", "wav"),
        ("audio/ogg", "ogg"),
        ("audio/webm", "webm"),
    ]:
        r = client.post(
            "/api/v1/capture/audio",
            content=b"x",
            headers={**AUTH, "Content-Type": content_type},
        )
        assert r.status_code == 202, content_type
        assert fake_gcs[-1]["ext"] == ext


def test_audio_capture_bad_content_type_415(client, fake_gcs):
    r = client.post(
        "/api/v1/capture/audio",
        content=b"x",
        headers={**AUTH, "Content-Type": "video/mp4"},
    )
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"
    assert fake_gcs == []


def test_image_capture_202_and_gcs_write(client, fake_gcs):
    r = client.post(
        "/api/v1/capture/image",
        json=_image_body(
            text="pricing table to compare later",
            source_url="https://example.com/pricing",
            title="Example — Pricing",
            source="web",
        ),
        headers=AUTH,
    )
    assert r.status_code == 202
    capture_id = r.json()["id"]
    assert fake_gcs == [
        {
            "capture_id": capture_id,
            "ext": "png",
            "data": PNG,
            "content_type": "image/png",
        }
    ]

    cap = client.get(f"/api/v1/captures/{capture_id}", headers=AUTH).json()["capture"]
    assert cap["kind"] == "image"
    assert cap["status"] == "pending"
    assert cap["source"] == "web"
    assert cap["image_gcs_uri"] == f"gs://test-bucket/captures/{capture_id}.png"
    assert cap["image_mime"] == "image/png"
    assert cap["text"] == "pricing table to compare later"
    assert cap["source_url"] == "https://example.com/pricing"
    assert cap["title"] == "Example — Pricing"


def test_image_capture_ext_mapping(client, fake_gcs):
    for mime, ext in [
        ("image/png", "png"),
        ("image/jpeg", "jpg"),
        ("image/webp", "webp"),
        ("image/gif", "gif"),
    ]:
        r = client.post(
            "/api/v1/capture/image", json=_image_body(mime=mime), headers=AUTH
        )
        assert r.status_code == 202, mime
        assert fake_gcs[-1]["ext"] == ext


def test_image_capture_requires_auth(client, fake_gcs):
    r = client.post("/api/v1/capture/image", json=_image_body())
    assert r.status_code == 401
    assert fake_gcs == []


def test_image_capture_bad_mime_415(client, fake_gcs):
    r = client.post(
        "/api/v1/capture/image", json=_image_body(mime="application/pdf"), headers=AUTH
    )
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"
    assert fake_gcs == []


def test_image_capture_bad_base64_400(client, fake_gcs):
    r = client.post(
        "/api/v1/capture/image",
        json={"image_base64": "not base64!!", "mime": "image/png"},
        headers=AUTH,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_base64"
    assert fake_gcs == []


def test_image_capture_empty_400(client, fake_gcs):
    r = client.post(
        "/api/v1/capture/image",
        json={"image_base64": "", "mime": "image/png"},
        headers=AUTH,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "empty_body"
    assert fake_gcs == []


def test_image_capture_too_large_413(client, fake_gcs):
    oversized = base64.b64encode(b"x" * (10 * 1024 * 1024 + 1)).decode()
    r = client.post(
        "/api/v1/capture/image",
        json={"image_base64": oversized, "mime": "image/png"},
        headers=AUTH,
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"
    assert fake_gcs == []


def test_capture_image_bytes_served_back(client, fake_gcs, monkeypatch):
    capture_id = client.post(
        "/api/v1/capture/image", json=_image_body(), headers=AUTH
    ).json()["id"]

    import memex.api.gcs

    monkeypatch.setattr(memex.api.gcs, "download", lambda uri: PNG)
    r = client.get(f"/api/v1/captures/{capture_id}/image", headers=AUTH)
    assert r.status_code == 200
    assert r.content == PNG
    assert r.headers["content-type"] == "image/png"


def test_capture_image_bytes_404_for_non_image(client, agent_stub):
    capture_id = client.post(
        "/api/v1/capture", json={"text": "hi"}, headers=AUTH
    ).json()["capture"]["id"]
    r = client.get(f"/api/v1/captures/{capture_id}/image", headers=AUTH)
    assert r.status_code == 404
def test_link_capture_happy_path(client, agent_stub):
    r = client.post(
        "/api/v1/capture/link",
        json={
            "url": "https://example.com/post?x=1",
            "title": "A post",
            "note": "read before the review",
        },
        headers=AUTH,
    )
    assert r.status_code == 201
    body = r.json()
    cap = body["capture"]
    assert cap["kind"] == "link"
    assert cap["url"] == "https://example.com/post?x=1"
    assert cap["title"] == "A post"
    assert cap["text"] == "read before the review"
    assert cap["status"] == "enriched"
    note = body["note"]
    assert note["kind"] == "link"
    assert note["body"].startswith("[A post](https://example.com/post?x=1)")
    assert "read-later" in note["tags"]
    assert agent_stub["enrich"] == [cap["id"]]


def test_link_capture_title_and_note_optional(client, agent_stub):
    r = client.post(
        "/api/v1/capture/link", json={"url": "https://example.com/"}, headers=AUTH
    )
    assert r.status_code == 201
    cap = r.json()["capture"]
    assert cap["title"] is None and cap["text"] is None


def test_link_capture_rejects_non_http_urls(client, agent_stub):
    for url in ["", "   ", "ftp://example.com/x", "javascript:alert(1)", "not a url"]:
        r = client.post("/api/v1/capture/link", json={"url": url}, headers=AUTH)
        assert r.status_code == 400, url
        assert r.json()["error"]["code"] == "invalid_url", url
    assert agent_stub["enrich"] == []


def test_link_capture_rejects_overlong_url(client, agent_stub):
    r = client.post(
        "/api/v1/capture/link",
        json={"url": "https://example.com/" + "a" * 2100},
        headers=AUTH,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_url"


def test_link_batch_creates_one_capture_per_link(client, agent_stub):
    r = client.post(
        "/api/v1/capture/links",
        json={
            "links": [
                {"url": "https://a.example/1", "title": "One"},
                {"url": "https://b.example/2", "title": "Two"},
            ],
            "source": "desktop",
        },
        headers=AUTH,
    )
    assert r.status_code == 201
    results = r.json()["results"]
    assert [x["url"] for x in results] == [
        "https://a.example/1",
        "https://b.example/2",
    ]
    assert [x["capture"]["source"] for x in results] == ["desktop", "desktop"]
    assert len(agent_stub["enrich"]) == 2
    assert len({x["note"]["id"] for x in results}) == 2


def test_link_batch_reports_per_link_failure_without_failing_the_batch(
    client, agent_stub
):
    r = client.post(
        "/api/v1/capture/links",
        json={
            "links": [
                {"url": "chrome://newtab"},
                {"url": "https://ok.example/"},
            ]
        },
        headers=AUTH,
    )
    assert r.status_code == 201
    bad, good = r.json()["results"]
    assert bad["error"]["code"] == "invalid_url"
    assert "capture" not in bad
    assert good["capture"]["status"] == "enriched"


def test_link_batch_empty_and_oversized_rejected(client, agent_stub):
    r = client.post("/api/v1/capture/links", json={"links": []}, headers=AUTH)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "empty_batch"

    r = client.post(
        "/api/v1/capture/links",
        json={"links": [{"url": f"https://e.example/{i}"} for i in range(21)]},
        headers=AUTH,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "batch_too_large"
    assert agent_stub["enrich"] == []


def test_get_capture_404(client):
    r = client.get("/api/v1/captures/does-not-exist", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
