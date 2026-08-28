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
The voice is your user's own; these are their personal notes.

Return JSON with:
- transcript: a faithful verbatim transcript of the speech.
- summary: one or two sentences capturing the point of the memo. Write it the
  way the user would jot it for themselves — direct and first-person-implied
  ("Buy replacement air filters this weekend", "Idea: batch the review loop"),
  never distant third person ("The speaker needs to...", "The user wants...").
- tags: 1-5 lowercase kebab-case topic tags.
- action_items: concrete to-dos the memo commits to or requests, each with a
  short imperative title. Empty list if none.
Do not invent content that is not in the audio.
"""

_INSTRUCTION_TEXT = """\
You are memex, a personal capture assistant. Below is a text note your user
captured for themselves. Treat the text as the transcript verbatim.

Return JSON with:
- transcript: the captured text, verbatim and unmodified.
- summary: one or two sentences capturing the point of the note. Write it the
  way the user would jot it for themselves — direct and first-person-implied
  ("Buy replacement air filters this weekend", "Idea: batch the review loop"),
  never distant third person ("The speaker needs to...", "The user wants...").
- tags: 1-5 lowercase kebab-case topic tags.
- action_items: concrete to-dos the note commits to or requests, each with a
  short imperative title. Empty list if none.
Do not invent content that is not in the note.
"""


_INSTRUCTION_IMAGE = """\
You are memex, a personal capture assistant. Attached is a screenshot your
user grabbed of something they were looking at, so they could come back to it.

Return JSON with:
- transcript: a faithful description of what the screenshot shows. Transcribe
  any text in it verbatim (that is usually the point of the capture), then
  describe the surrounding interface or image in a sentence or two.
- summary: one or two sentences on what this capture is, written the way the
  user would jot it for themselves — direct and first-person-implied
  ("Pricing table for the Fly.io machines plan", "Stack trace from the failing
  deploy"), never distant third person ("The user captured...").
- tags: 1-5 lowercase kebab-case topic tags.
- action_items: concrete to-dos the screenshot or the user's own note asks
  for, each with a short imperative title and, when a time reference is given,
  its verbatim wording as due_hint. Empty list if none.
Do not invent content that is not in the image.
"""


def _image_context(
    caption: str | None, source_url: str | None, title: str | None
) -> str:
    """The user's own words about the capture, appended to the instruction."""
    lines = []
    if caption:
        lines.append(f"The user's note on this capture: {caption}")
    if title:
        lines.append(f"Page title: {title}")
    if source_url:
        lines.append(f"Page URL: {source_url}")
    return ("\n" + "\n".join(lines) + "\n") if lines else ""


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


def enrich_image(
    image: bytes,
    mime_type: str,
    *,
    caption: str | None = None,
    source_url: str | None = None,
    title: str | None = None,
) -> EnrichmentResult:
    """Enrich a screenshot capture — Gemini reads the image directly.

    Runs on the analysis model, not the transcription tier: describing an
    interface is reasoning work, not dictation.
    """
    response = _client().models.generate_content(
        model=settings().model,
        contents=[
            types.Part.from_bytes(data=image, mime_type=mime_type),
            _INSTRUCTION_IMAGE + _image_context(caption, source_url, title),
        ],
        config=_config(),
    )
    return EnrichmentResult.model_validate_json(response.text)


def enrich_audio(audio: bytes, mime_type: str) -> EnrichmentResult:
    """Enrich an audio capture — Gemini is audio-native, no STT service."""
    response = _client().models.generate_content(
        model=settings().transcribe_model,
        contents=[
            types.Part.from_bytes(data=audio, mime_type=mime_type),
            _INSTRUCTION_AUDIO,
        ],
        config=_config(),
    )
    return EnrichmentResult.model_validate_json(response.text)
