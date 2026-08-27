"""Bearer-key auth on /api/v1/*."""

from tests.conftest import AUTH


def test_healthz_needs_no_auth(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_missing_bearer_rejected(client):
    r = client.get("/api/v1/notes")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_unknown_key_rejected(client):
    r = client.get("/api/v1/notes", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_valid_key_accepted(client):
    r = client.get("/api/v1/notes", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"notes": []}


def test_device_id_resolved_from_key(client, agent_stub):
    r = client.post(
        "/api/v1/capture",
        json={"text": "hello"},
        headers={"Authorization": "Bearer phone-key"},
    )
    assert r.status_code == 201
    assert r.json()["capture"]["device_id"] == "phone"


def test_health_alias_needs_no_auth(client):
    assert client.get("/health").status_code == 200
