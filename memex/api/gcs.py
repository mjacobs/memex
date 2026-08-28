"""GCS blob storage for audio and image captures.

Every capture blob lands at `captures/<capture_id>.<ext>` in the one bucket,
because the Eventarc finalize trigger that kicks off enrichment is scoped to
that prefix — an image dropped anywhere else would never be enriched.

Tests monkeypatch upload_audio / upload_image.
"""

from functools import lru_cache

from memex.config import settings


@lru_cache
def _client():
    from google.cloud import storage

    return storage.Client(project=settings().project)


def _object_name(capture_id: str, ext: str) -> str:
    return f"captures/{capture_id}.{ext}"


def _upload(capture_id: str, ext: str, data: bytes, content_type: str) -> str:
    bucket_name = settings().audio_bucket
    object_name = _object_name(capture_id, ext)
    bucket = _client().bucket(bucket_name)
    bucket.blob(object_name).upload_from_string(data, content_type=content_type)
    return f"gs://{bucket_name}/{object_name}"


def audio_uri(capture_id: str, ext: str) -> str:
    """Deterministic URI for a capture's audio object."""
    return f"gs://{settings().audio_bucket}/{_object_name(capture_id, ext)}"


def image_uri(capture_id: str, ext: str) -> str:
    """Deterministic URI for a capture's image object."""
    return f"gs://{settings().audio_bucket}/{_object_name(capture_id, ext)}"


def upload_audio(capture_id: str, ext: str, data: bytes, content_type: str) -> str:
    """Upload raw audio to gs://<bucket>/captures/<capture_id>.<ext>; return URI."""
    return _upload(capture_id, ext, data, content_type)


def upload_image(capture_id: str, ext: str, data: bytes, content_type: str) -> str:
    """Upload raw image bytes to gs://<bucket>/captures/<capture_id>.<ext>."""
    return _upload(capture_id, ext, data, content_type)


def download(gcs_uri: str) -> bytes:
    """Read an object back out by gs:// URI."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"not a gs:// uri: {gcs_uri}")
    bucket_name, _, blob_name = gcs_uri.removeprefix("gs://").partition("/")
    if not blob_name:
        raise ValueError(f"gs:// uri has no object name: {gcs_uri}")
    return _client().bucket(bucket_name).blob(blob_name).download_as_bytes()
