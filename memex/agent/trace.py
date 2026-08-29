"""ADK event → contract trace mapping, shared by routines and chat.

One spelling of "what lands in a stored trace" (docs/contracts.md Trace):
both the routine fire path and the chat turn path stream ADK events through
this mapping, so the SPA replays either from the same compact format.
"""

from memex.models import TraceEvent
from memex.store.firestore import now


def trace_events_from_adk_event(event: object) -> list[TraceEvent]:
    """Map one ADK event to zero or more contract trace events."""
    out: list[TraceEvent] = []
    content = getattr(event, "content", None)
    if content is None or not getattr(content, "parts", None):
        return out
    role = "user" if content.role == "user" else "model"
    for part in content.parts:
        text = getattr(part, "text", None)
        if text:
            out.append(TraceEvent(t=now(), role=role, text=text))
        fc = getattr(part, "function_call", None)
        if fc is not None:
            out.append(
                TraceEvent(t=now(), role="model", tool=fc.name, args=dict(fc.args or {}))
            )
        fr = getattr(part, "function_response", None)
        if fr is not None:
            out.append(
                TraceEvent(
                    t=now(), role="tool", tool=fr.name, result=dict(fr.response or {})
                )
            )
    return out
