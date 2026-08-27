"""Text and audio capture paths."""

from tests.conftest import AUTH


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


def test_get_capture_404(client):
    r = client.get("/api/v1/captures/does-not-exist", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
