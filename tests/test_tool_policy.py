"""The chat tool policy table: classification, resolution, session trust.

Nothing enforces this yet (docs/chat-tool-policy.md step 1), so these tests
are the whole contract — including the coverage test, which fails the moment
a tool joins CHAT_TOOLS without a risk class.
"""

import pytest

from memex.agent import tool_policy
from memex.agent.tools import CHAT_TOOLS


def test_every_chat_tool_is_classified():
    """A new chat tool must not reach the model unclassified."""
    unclassified = []
    for name in tool_policy.chat_tool_names():
        try:
            tool_policy.classify(name)
        except tool_policy.UnclassifiedToolError:
            unclassified.append(name)
    assert unclassified == [], (
        "these tools are offered to chat but have no classification in "
        f"memex/agent/tool_policy.py: {unclassified}"
    )
    assert len(tool_policy.chat_tool_names()) == len(CHAT_TOOLS)


def test_classifications_match_the_spec():
    assert tool_policy.classify("list_tasks") == "read"
    assert tool_policy.classify("list_recent_notes") == "read"
    assert tool_policy.classify("search_notes") == "read"
    assert tool_policy.classify("create_note") == "write"
    assert tool_policy.classify("update_note") == "write"
    assert tool_policy.classify("update_task") == "write"
    assert tool_policy.classify("queue_approval") == "write"
    assert tool_policy.classify("start_research") == "external"


def test_classify_refuses_an_unknown_tool():
    with pytest.raises(tool_policy.UnclassifiedToolError):
        tool_policy.classify("delete_everything")


def test_reads_run_and_the_rest_ask():
    assert tool_policy.resolve("search_notes") == "allow"
    assert tool_policy.resolve("update_note") == "ask"
    assert tool_policy.resolve("start_research") == "ask"
    assert not tool_policy.requires_confirmation("list_tasks")
    assert tool_policy.requires_confirmation("update_task")
    assert tool_policy.requires_confirmation("start_research")


def test_session_trust_downgrades_an_asking_tool():
    trusted = {"update_note"}
    assert tool_policy.resolve("update_note", trusted) == "allow"
    assert not tool_policy.requires_confirmation("update_note", trusted)
    # Trust is per tool, not blanket.
    assert tool_policy.resolve("update_task", trusted) == "ask"
    assert tool_policy.resolve("start_research", trusted) == "ask"


def test_trust_cannot_smuggle_in_an_unknown_tool():
    """Trust is not a way around the table: an unclassified name still fails."""
    with pytest.raises(tool_policy.UnclassifiedToolError):
        tool_policy.resolve("delete_everything", {"delete_everything"})
