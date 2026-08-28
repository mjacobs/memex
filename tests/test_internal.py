"""Internal endpoints: OIDC gating, CloudEvent mapping, routine ticks."""

from memex.ids import new_ulid
from memex.models import Capture
from memex.store import firestore as store


def _make_audio_capture() -> Capture:
    capture = Capture(
        id=new_ulid(),
        created_at=store.now(),
        device_id="dev",
        kind="audio",
        audio_gcs_uri="gs://test-bucket/captures/x.m4a",
        audio_mime="audio/mp4",
        status="pending",
    )
    store.put(capture)
    return capture


def test_internal_rejected_without_oidc_when_service_url_set(client, monkeypatch):
    # memex.config.Settings reads env at class-definition time, so setenv is
    # too late here — patch the settings accessor the auth module imported.
    import memex.api.auth

    monkeypatch.setattr(
        memex.api.auth,
        "settings",
        lambda: memex.config.Settings(service_url="https://memex.example.run.app"),
    )

    r = client.post("/internal/routines/daily_review/tick")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"

    # a device bearer key is not a Google OIDC token -> still rejected
    r2 = client.post(
        "/internal/routines/daily_review/tick",
        headers={"Authorization": "Bearer dev-key"},
    )
    assert r2.status_code == 401


def test_enrich_maps_object_name_to_capture(client, agent_stub):
    capture = _make_audio_capture()
    r = client.post(
        "/internal/enrich",
        json={"bucket": "test-bucket", "name": f"captures/{capture.id}.m4a"},
    )
    assert r.status_code == 200
    assert agent_stub["enrich"] == [capture.id]
    assert r.json()["note_id"]


def test_enrich_structured_cloudevent_payload(client, agent_stub):
    capture = _make_audio_capture()
    r = client.post(
        "/internal/enrich",
        json={"data": {"bucket": "test-bucket", "name": f"captures/{capture.id}.m4a"}},
    )
    assert r.status_code == 200
    assert agent_stub["enrich"] == [capture.id]


def test_enrich_ignores_non_capture_objects(client, agent_stub):
    r = client.post("/internal/enrich", json={"name": "other/thing.txt"})
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    assert agent_stub["enrich"] == []


def test_enrich_unknown_capture_404(client, agent_stub):
    r = client.post("/internal/enrich", json={"name": "captures/unknown.m4a"})
    assert r.status_code == 404


def test_enrich_bad_event_400(client, fs):
    r = client.post("/internal/enrich", json={"nope": True})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_event"


def test_routine_tick(client, agent_stub):
    r = client.post("/internal/routines/nightly_digest/tick")
    assert r.status_code == 200
    assert r.json()["status"] == "succeeded"
    assert agent_stub["routine"] == ["nightly_digest"]

    runs = client.get(
        "/api/v1/routines/runs", headers={"Authorization": "Bearer dev-key"}
    )
    listed = runs.json()["runs"]
    assert len(listed) == 1
    assert "trace" not in listed[0]

    detail = client.get(
        f"/api/v1/routines/runs/{listed[0]['id']}",
        headers={"Authorization": "Bearer dev-key"},
    )
    assert detail.status_code == 200
    assert "trace" in detail.json()["run"]


def test_unknown_routine_404(client, agent_stub):
    r = client.post("/internal/routines/hourly_chaos/tick")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "unknown_routine"


def test_routine_tick_agent_unavailable_503(client, agent_missing, fs):
    r = client.post("/internal/routines/daily_review/tick")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "agent_unavailable"


def test_internal_oidc_claims_checks(client, monkeypatch, agent_stub):
    """Verified tokens must match an allowed audience AND an invoker SA."""
    import memex.api.auth as auth_mod
    import memex.config

    cfg = memex.config.Settings(
        service_url="https://memex-123.us-central1.run.app",
        internal_invokers=("memex-trigger@p.iam.gserviceaccount.com",),
    )
    monkeypatch.setattr(auth_mod, "settings", lambda: cfg)

    claims = {}

    class FakeIdToken:
        @staticmethod
        def verify_oauth2_token(token, req, audience=None):
            return dict(claims)

    import google.oauth2.id_token as real

    monkeypatch.setattr(real, "verify_oauth2_token", FakeIdToken.verify_oauth2_token)
    jwt = "x.y.z"
    hdr = {"Authorization": f"Bearer {jwt}"}
    tick = "/internal/routines/daily_review/tick"

    # wrong audience
    claims.update(
        aud="https://evil.example.com",
        email="memex-trigger@p.iam.gserviceaccount.com",
        email_verified=True,
    )
    assert client.post(tick, headers=hdr).status_code == 401

    # right audience (legacy host form counts via request host), wrong caller
    claims["aud"] = "https://testserver"
    claims["email"] = "attacker@other.iam.gserviceaccount.com"
    assert client.post(tick, headers=hdr).status_code == 401

    # full push-endpoint audience (host + path) also passes
    claims["aud"] = "https://testserver/internal/routines/daily_review/tick"
    claims["email"] = "memex-trigger@p.iam.gserviceaccount.com"
    assert client.post(tick, headers=hdr).status_code == 200

    # configured audience + allowed invoker passes auth
    claims["aud"] = "https://memex-123.us-central1.run.app"
    claims["email"] = "memex-trigger@p.iam.gserviceaccount.com"
    assert client.post(tick, headers=hdr).status_code == 200


def test_routine_tick_failed_run_returns_500(client, monkeypatch, fs):
    """A failed routine must surface non-2xx so Cloud Scheduler retries."""
    import sys
    import types as types_mod

    module = types_mod.ModuleType("memex.agent.service")
    module.run_routine = lambda routine: {"status": "failed", "error": "boom"}
    monkeypatch.setitem(sys.modules, "memex.agent.service", module)

    r = client.post("/internal/routines/daily_review/tick")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "routine_failed"
