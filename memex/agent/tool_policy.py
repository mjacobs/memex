"""What each chat tool can cost, and whether it should pause for a human.

The gate is a risk class, not provenance (docs/chat-tool-policy.md, after
obsidian-gemini's `tool-policy.ts`): a poisoned page's instruction and the
user's own read identically to the model, so the question this module answers
is "how expensive is this call if it was not asked for" rather than "who asked
for it". `read` tools run; `write` and `external` tools ask.

Pure classification — nothing here reads session state or blocks a call. The
`require_confirmation` predicate that consumes `resolve` arrives in a later
step; until then this is a table plus a resolver, and the coverage test in
tests/test_tool_policy.py is what keeps a newly added chat tool from slipping
in unclassified.
"""

from collections.abc import Callable, Iterable
from typing import Literal

from memex.agent.tools import CHAT_TOOLS

# What a call can cost. `read` returns stored data and changes nothing;
# `write` mutates the user's memex; `external` leaves the machine — it spends
# money and hands a note to another service, so it is worth asking about even
# though it writes nothing directly.
Classification = Literal["read", "write", "external"]

# What the policy decides to do about a call. `deny` is unused by chat today
# and exists because routines are the same policy with a different answer:
# a headless session denies what an attended one asks about.
Permission = Literal["allow", "ask", "deny"]

_CLASSIFICATIONS: dict[str, Classification] = {
    "list_tasks": "read",
    "list_recent_notes": "read",
    "search_notes": "read",
    "create_note": "write",
    "update_note": "write",
    "update_task": "write",
    # A proposal, not a mutation — but it still lands a durable doc the user
    # has to triage, so it is classed with the writes rather than the reads.
    "queue_approval": "write",
    "start_research": "external",
}

_DEFAULT_PERMISSIONS: dict[Classification, Permission] = {
    "read": "allow",
    "write": "ask",
    "external": "ask",
}


class UnclassifiedToolError(LookupError):
    """A tool was offered to chat without a classification.

    Raised rather than defaulted so the omission surfaces at the call site
    (and in the coverage test) instead of quietly picking a permission.
    """


def classify(tool_name: str) -> Classification:
    """The risk class of one tool, by the name the model calls it by."""
    try:
        return _CLASSIFICATIONS[tool_name]
    except KeyError:
        raise UnclassifiedToolError(
            f"chat tool {tool_name!r} has no classification in memex.agent.tool_policy"
        ) from None


def resolve(tool_name: str, trusted_tools: Iterable[str] = ()) -> Permission:
    """Decide what to do about a call to `tool_name`.

    `trusted_tools` is the session-scoped "don't ask me again" set: a tool
    the user has already blessed in this conversation resolves to `allow`
    whatever its class. Nothing persists that set yet — it is a parameter so
    the later `require_confirmation` predicate can pass one in, and trust
    that never outlives the session it was granted in is the whole point.
    """
    # Classify first: an unknown name is an error even when something put it
    # in the trust set, so trust can never be the way an unclassified tool
    # gets a permission.
    permission = _DEFAULT_PERMISSIONS[classify(tool_name)]
    if tool_name in trusted_tools:
        return "allow"
    return permission


def requires_confirmation(tool_name: str, trusted_tools: Iterable[str] = ()) -> bool:
    """True when a call must pause for the user — the shape ADK's
    `require_confirmation` predicate wants."""
    return resolve(tool_name, trusted_tools) == "ask"


def chat_tool_names() -> list[str]:
    """The names ADK will call chat's tools by, in toolset order."""
    return [_tool_name(fn) for fn in CHAT_TOOLS]


def _tool_name(fn: Callable[..., object]) -> str:
    # ADK's FunctionTool names a tool after the function it wraps.
    return fn.__name__
