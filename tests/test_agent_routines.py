"""Routine prompt content checks (no LLM calls): both routine prompts must
teach the evidence-citation link convention so digests/reviews stay
traceable back to the source notes rendered by the SPA's Markdown component.
"""

from memex.agent.routines import ROUTINE_PROMPTS


def test_all_routines_have_prompts() -> None:
    assert set(ROUTINE_PROMPTS) == {"daily_review", "nightly_digest"}


def test_prompts_teach_the_note_link_convention() -> None:
    for routine, prompt in ROUTINE_PROMPTS.items():
        assert "(#/notes/" in prompt, f"{routine} prompt missing the link convention"
        assert "[note]" in prompt or "[<short label>]" in prompt, (
            f"{routine} prompt missing an example link"
        )
        assert "never invent" in prompt.lower() or "never invent or" in prompt.lower(), (
            f"{routine} prompt does not forbid inventing ids"
        )


def test_daily_review_prompt_cites_source_note_id() -> None:
    assert "source_note_id" in ROUTINE_PROMPTS["daily_review"]


def test_nightly_digest_prompt_cites_note_id() -> None:
    prompt = ROUTINE_PROMPTS["nightly_digest"]
    assert "\"id\"" in prompt or "'id'" in prompt
