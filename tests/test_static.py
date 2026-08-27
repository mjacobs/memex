"""Static SPA serving: mounted only when memex/static exists; SPA fallback."""

from fastapi.testclient import TestClient

import memex.api.app


def test_spa_fallback_serves_index(fs, monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<html>memex</html>")
    (tmp_path / "app.js").write_text("console.log('hi')")
    monkeypatch.setattr(memex.api.app, "STATIC_DIR", tmp_path)
    client = TestClient(memex.api.app.create_app())

    assert client.get("/").text == "<html>memex</html>"
    assert client.get("/app.js").text == "console.log('hi')"
    # unknown client-side route falls back to index.html
    assert client.get("/tasks/somewhere/deep").text == "<html>memex</html>"


def test_no_static_dir_returns_contract_404(fs, client):
    r = client.get("/definitely-not-here")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
