"""GCS blob storage for audio and image captures.

Audio and images live in sibling buckets so retention is a bucket-level
policy: recordings age out after their transcript is extracted, screenshots
are the note's content and are kept until the note is deleted. Each bucket
has its own Eventarc finalize trigger driving /internal/enrich, and objects
land at `captures/<capture_id>.<ext>` in both.

The images bucket falls back to the audio bucket when MEMEX_IMAGES_BUCKET is
unset, so a code deploy that races the terraform apply still lands images
somewhere with a working trigger.

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


def _images_bucket() -> str:
    return settings().images_bucket or settings().audio_bucket


def _upload(bucket_name: str, capture_id: str, ext: str, data: bytes, content_type: str) -> str:
    object_name = _object_name(capture_id, ext)
    bucket = _client().bucket(bucket_name)
    bucket.blob(object_name).upload_from_string(data, content_type=content_type)
    return f"gs://{bucket_name}/{object_name}"


def audio_uri(capture_id: str, ext: str) -> str:
    """Deterministic URI for a capture's audio object."""
    return f"gs://{settings().audio_bucket}/{_object_name(capture_id, ext)}"


def image_uri(capture_id: str, ext: str) -> str:
    """Deterministic URI for a capture's image object."""
    return f"gs://{_images_bucket()}/{_object_name(capture_id, ext)}"


def upload_audio(capture_id: str, ext: str, data: bytes, content_type: str) -> str:
    """Upload raw audio to gs://<bucket>/captures/<capture_id>.<ext>; return URI."""
    return _upload(settings().audio_bucket, capture_id, ext, data, content_type)


def upload_image(capture_id: str, ext: str, data: bytes, content_type: str) -> str:
    """Upload raw image bytes to gs://<bucket>/captures/<capture_id>.<ext>."""
    return _upload(_images_bucket(), capture_id, ext, data, content_type)


def delete(gcs_uri: str) -> None:
    """Delete an object by gs:// URI. Missing objects are fine — a recording
    may already have aged out via the bucket lifecycle rule."""
    from google.cloud.exceptions import NotFound

    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"not a gs:// uri: {gcs_uri}")
    bucket_name, _, blob_name = gcs_uri.removeprefix("gs://").partition("/")
    if not blob_name:
        raise ValueError(f"gs:// uri has no object name: {gcs_uri}")
    try:
        _client().bucket(bucket_name).blob(blob_name).delete()
    except NotFound:
        pass


def download(gcs_uri: str) -> bytes:
    """Read an object back out by gs:// URI."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"not a gs:// uri: {gcs_uri}")
    bucket_name, _, blob_name = gcs_uri.removeprefix("gs://").partition("/")
    if not blob_name:
        raise ValueError(f"gs:// uri has no object name: {gcs_uri}")
    return _client().bucket(bucket_name).blob(blob_name).download_as_bytes()
