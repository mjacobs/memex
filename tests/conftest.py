"""Shared fixtures for the API contract tests.

The store fixture runs against the Firestore emulator when
FIRESTORE_EMULATOR_HOST is set; otherwise it substitutes an in-memory fake
behind memex.store.firestore.db (all access goes through those helpers).
"""

import os
import sys
import types

import pytest
from fastapi.testclient import TestClient

import memex.config
from memex.ids import new_ulid
from memex.models import Capture, Note, RoutineRun, Task
from memex.store import firestore as store
from tests.fake_firestore import FakeFirestoreClient

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))

DEVICE_KEYS = {"dev": "dev-key", "phone": "phone-key"}
AUTH = {"Authorization": "Bearer dev-key"}


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    monkeypatch.setenv(
        "MEMEX_DEVICE_KEYS_JSON", '{"dev": "dev-key", "phone": "phone-key"}'
    )
    monkeypatch.setenv("MEMEX_AUDIO_BUCKET", "test-bucket")
    monkeypatch.delenv("MEMEX_SERVICE_URL", raising=False)
    memex.config.settings.cache_clear()
    yield
    memex.config.settings.cache_clear()


@pytest.fixture
def fs(monkeypatch):
    if EMULATOR:
        store.db.cache_clear()
        client = store.db()
        for coll in store.COLLECTIONS.values():
            for doc in client.collection(coll).list_documents():
                doc.delete()
        yield client
        store.db.cache_clear()
    else:
        fake = FakeFirestoreClient()
        monkeypatch.setattr(store, "db", lambda: fake)
        yield fake


@pytest.fixture
def client(fs):
    from memex.api.app import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture
def agent_stub(monkeypatch):
    """Install a fake memex.agent.service at the W3 seam."""
    calls = {"enrich": [], "routine": []}

    def enrich_capture(capture_id: str) -> dict:
        calls["enrich"].append(capture_id)
        capture = store.get(Capture, capture_id)
        assert capture is not None
        note = Note(
            id=new_ulid(),
            created_at=store.now(),
            kind="capture",
            capture_id=capture_id,
            body=capture.text or "(audio)",
            summary="stub summary",
            tags=["stub"],
        )
        task = Task(
            id=new_ulid(),
            title="stub task",
            created_at=store.now(),
            updated_at=store.now(),
            source_note_id=note.id,
        )
        note.task_ids = [task.id]
        store.put(task)
        store.put(note)
        store.update(
            Capture, capture_id, {"status": "enriched", "note_id": note.id}
        )
        return {"note_id": note.id, "task_ids": [task.id]}

    def run_routine(routine: str) -> dict:
        calls["routine"].append(routine)
        run = RoutineRun(
            id=new_ulid(), routine=routine, fired_at=store.now(), status="succeeded"
        )
        store.put(run)
        return {"run_id": run.id, "status": "succeeded"}

    module = types.ModuleType("memex.agent.service")
    module.enrich_capture = enrich_capture
    module.run_routine = run_routine
    monkeypatch.setitem(sys.modules, "memex.agent.service", module)
    return calls


@pytest.fixture
def agent_missing(monkeypatch):
    """Force the lazy seam import to fail (503 path), even post-W3-merge."""
    monkeypatch.setitem(sys.modules, "memex.agent.service", None)


@pytest.fixture
def fake_gcs(monkeypatch):
    """Record GCS uploads instead of talking to real GCS."""
    uploads: list[dict] = []

    def upload_audio(capture_id: str, ext: str, data: bytes, content_type: str) -> str:
        uploads.append(
            {
                "capture_id": capture_id,
                "ext": ext,
                "data": data,
                "content_type": content_type,
            }
        )
        return f"gs://test-bucket/captures/{capture_id}.{ext}"

    import memex.api.gcs

    monkeypatch.setattr(memex.api.gcs, "upload_audio", upload_audio)
    return uploads
