"""EnrichmentResult JSON-schema round-trip (no LLM-output asserts)."""

import json

from memex.models import ActionItem, EnrichmentResult


def test_enrichment_schema_has_contract_fields() -> None:
    schema = EnrichmentResult.model_json_schema()
    assert set(schema["required"]) == {"transcript", "summary", "tags", "action_items"}
    # The schema must be pure JSON (what response_json_schema receives).
    json.dumps(schema)


def test_enrichment_result_round_trips() -> None:
    original = EnrichmentResult(
        transcript="call the plumber by friday",
        summary="Call the plumber.",
        tags=["home", "errands"],
        action_items=[ActionItem(title="Call the plumber", due_hint="by friday")],
    )
    parsed = EnrichmentResult.model_validate_json(original.model_dump_json())
    assert parsed == original


def test_enrichment_result_parses_model_style_json() -> None:
    # The exact shape the verified Vertex call returns (contracts.md).
    payload = {
        "transcript": "ship the memex demo",
        "summary": "Ship the demo.",
        "tags": ["memex"],
        "action_items": [{"title": "Ship the demo"}],
    }
    result = EnrichmentResult.model_validate(payload)
    assert result.action_items[0].due_hint is None
