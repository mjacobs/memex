"""The single structured-output enrichment call (contracts.md, verified shape).

One Vertex `generate_content` call turns a capture (text or audio bytes) into
an `EnrichmentResult` — transcript, summary, tags, action items. No tool loop:
tools are for routine sessions only.
"""

from functools import lru_cache

from google import genai
from google.genai import types

from memex.config import settings
from memex.models import EnrichmentResult

_INSTRUCTION_AUDIO = """\
You are memex, a personal capture assistant. Listen to the attached audio memo.

Return JSON with:
- transcript: a faithful verbatim transcript of the speech.
- summary: one or two sentences capturing the point of the memo.
- tags: 1-5 lowercase kebab-case topic tags.
- action_items: concrete to-dos the speaker committed to or requested, each
  with a short imperative title and, when the speaker gave a time reference,
  its verbatim wording as due_hint (e.g. "by Friday"). Empty list if none.
Do not invent content that is not in the audio.
"""

_INSTRUCTION_TEXT = """\
You are memex, a personal capture assistant. The user captured the following
text note. Treat the text as the transcript verbatim.

Return JSON with:
- transcript: the captured text, verbatim and unmodified.
- summary: one or two sentences capturing the point of the note.
- tags: 1-5 lowercase kebab-case topic tags.
- action_items: concrete to-dos the note commits to or requests, each with a
  short imperative title and, when the note gives a time reference, its
  verbatim wording as due_hint (e.g. "by Friday"). Empty list if none.
Do not invent content that is not in the note.
"""


@lru_cache
def _client() -> genai.Client:
    return genai.Client(
        vertexai=True, project=settings().project, location=settings().location
    )


def _config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=EnrichmentResult.model_json_schema(),
    )


def enrich_text(text: str) -> EnrichmentResult:
    """Enrich a text capture; the model must echo the text as the transcript."""
    response = _client().models.generate_content(
        model=settings().model,
        contents=[_INSTRUCTION_TEXT, text],
        config=_config(),
    )
    return EnrichmentResult.model_validate_json(response.text)


def enrich_audio(audio: bytes, mime_type: str) -> EnrichmentResult:
    """Enrich an audio capture — Gemini is audio-native, no STT service."""
    response = _client().models.generate_content(
        model=settings().model,
        contents=[
            types.Part.from_bytes(data=audio, mime_type=mime_type),
            _INSTRUCTION_AUDIO,
        ],
        config=_config(),
    )
    return EnrichmentResult.model_validate_json(response.text)
