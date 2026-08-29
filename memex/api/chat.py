"""/api/v1/chat routes per docs/contracts.md (WS-chat owns this module).

Sessions are plain CRUD over `chat_sessions`. The messages endpoint runs one
agent turn and streams it as `text/event-stream`: one `event: trace` per
TraceEvent as the turn executes, then `event: done` with the updated session
summary. The turn's events are appended to the stored trace afterwards —
the stream is the live view, the stored trace is the record.
"""

import json
import logging

import anyio.to_thread
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from memex.api.auth import require_device
from memex.api.common import ApiError, dump, error_body
from memex.ids import new_ulid
from memex.models import ChatSession, TraceEvent
from memex.store import firestore as store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", dependencies=[Depends(require_device)])

# "First user message, truncated" (contracts.md) — enough to label a session
# in the sidebar without storing an essay twice.
TITLE_MAX = 80


@router.post("/sessions", status_code=201)
def create_session() -> dict:
    session = ChatSession(id=new_ulid(), created_at=store.now(), updated_at=store.now())
    store.put(session)
    return {"session": dump(session)}


@router.get("/sessions")
def list_sessions(limit: int = 20) -> dict:
    sessions = store.list_chat_sessions(limit=limit)
    return {"sessions": [dump(s, include_trace=False) for s in sessions]}


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    session = store.get(ChatSession, session_id)
    if session is None:
        raise ApiError(404, "not_found", f"chat session {session_id} not found")
    return {"session": dump(session)}


class MessageIn(BaseModel):
    text: str


def _sse(event: str, data: dict) -> str:
    # json.dumps never emits a bare newline, so one data: line is the frame.
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, body: MessageIn) -> StreamingResponse:
    text = body.text.strip()
    if not text:
        raise ApiError(400, "empty_text", "text must be non-empty")
    session = store.get(ChatSession, session_id)
    if session is None:
        raise ApiError(404, "not_found", f"chat session {session_id} not found")
    # Lazy seam import, like /capture's enrichment: tests stub the module,
    # and a deploy without the agent package degrades to a clean 503.
    try:
        from memex.agent.chat import run_chat_turn
    except ImportError as exc:
        raise ApiError(
            503, "agent_unavailable", "chat agent is not available"
        ) from exc

    async def stream():
        events: list[TraceEvent] = []
        failure: Exception | None = None
        try:
            async for event in run_chat_turn(session_id, text):
                events.append(event)
                yield _sse("trace", event.model_dump(mode="json"))
        except Exception as exc:
            # The stream is already open (200 sent), so a crashed turn is
            # reported in-band; whatever the turn did still gets recorded.
            logger.exception("chat turn failed for session %s", session_id)
            failure = exc
        if events:
            await anyio.to_thread.run_sync(store.append_chat_trace, session_id, events)
            if session.title is None:
                await anyio.to_thread.run_sync(
                    store.update, ChatSession, session_id, {"title": text[:TITLE_MAX]}
                )
        if failure is not None:
            yield _sse("error", error_body("chat_turn_failed", str(failure)))
            return
        updated = await anyio.to_thread.run_sync(store.get, ChatSession, session_id)
        assert updated is not None
        yield _sse("done", {"session": dump(updated, include_trace=False)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store"},
    )
