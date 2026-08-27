"""GCS upload for audio captures (tests monkeypatch upload_audio)."""

from functools import lru_cache

from memex.config import settings


@lru_cache
def _client():
    from google.cloud import storage

    return storage.Client(project=settings().project)


def upload_audio(capture_id: str, ext: str, data: bytes, content_type: str) -> str:
    """Upload raw audio to gs://<bucket>/captures/<capture_id>.<ext>; return URI."""
    bucket_name = settings().audio_bucket
    object_name = f"captures/{capture_id}.{ext}"
    bucket = _client().bucket(bucket_name)
    bucket.blob(object_name).upload_from_string(data, content_type=content_type)
    return f"gs://{bucket_name}/{object_name}"
