"""Contract smoke: the shared models round-trip the frozen shapes."""

from datetime import UTC, datetime

from memex.ids import new_ulid
from memex.models import Approval, EnrichmentResult


def test_ulid_is_lowercase_sortable():
    a, b = new_ulid(), new_ulid()
    assert a == a.lower() and len(a) == 26
    assert a < b  # monotonic within a process


def test_enrichment_result_matches_verified_shape():
    r = EnrichmentResult.model_validate(
        {
            "transcript": "buy milk",
            "summary": "Buy milk.",
            "tags": ["errands"],
            "action_items": [{"title": "Buy milk"}],
        }
    )
    assert r.action_items[0].due_hint is None


def test_approval_action_discriminates():
    a = Approval.model_validate(
        {
            "id": new_ulid(),
            "created_at": datetime.now(UTC),
            "action": {"type": "task_update", "task_id": "x", "changes": {"status": "done"}},
            "reason": "stale",
        }
    )
    assert a.action.type == "task_update"
    assert a.status == "pending"
