"""Does resuming a confirmation need the WHOLE session, or just the pending bits?

Seeds a fresh session the way memex does today (text turns only) plus ONLY the
events that mention the pending confirmation, and sees whether it resumes.
"""
import asyncio
import json
from collections.abc import AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.events import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types
from pydantic import Field

executed = []


def update_note(note_id: str, summary: str) -> dict:
    executed.append(note_id)
    return {"ok": True}


class ScriptedLlm(BaseLlm):
    model: str = "scripted"
    turns: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)

    async def generate_content_async(self, r, stream=False) -> AsyncGenerator[LlmResponse]:
        self.seen.append(r)
        yield self.turns[min(len(self.seen) - 1, len(self.turns) - 1)]


def fc(n, a):
    return LlmResponse(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(id="fc-1", name=n, args=a))]))


def txt(t):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=t)]))


def agent(llm):
    return LlmAgent(name="memex_chat", model=llm, instruction="t",
                    tools=[FunctionTool(update_note, require_confirmation=True)])


async def main():
    # --- turn 1, as it would really happen ---------------------------------
    svc = InMemorySessionService()
    s = await svc.create_session(app_name="memex", user_id="u")
    llm = ScriptedLlm(turns=[fc("update_note", {"note_id": "n-1", "summary": "s"}), txt("ok")])
    confirm = None
    async for ev in Runner(agent=agent(llm), app_name="memex", session_service=svc).run_async(
            user_id="u", session_id=s.id,
            new_message=types.Content(role="user", parts=[types.Part(text="fix n-1")])):
        confirm = next((f for f in ev.get_function_calls()
                        if f.name == "adk_request_confirmation"), confirm)

    stored = await svc.get_session(app_name="memex", user_id="u", session_id=s.id)
    print("full session events:", len(stored.events))

    # Which events actually mention the pending confirmation?
    pending = [
        e for e in stored.events
        if any(f.name == "adk_request_confirmation" or f.id == "fc-1"
               for f in e.get_function_calls())
    ]
    wire = [json.loads(e.model_dump_json(exclude_none=True)) for e in pending]
    print("events mentioning the confirmation:", len(pending),
          "| bytes:", len(json.dumps(wire)))
    for e in pending:
        print("   kept:", [f.name for f in e.get_function_calls()])

    # --- turn 2: today's text seeding + ONLY those events -------------------
    fresh = InMemorySessionService()
    s2 = await fresh.create_session(app_name="memex", user_id="u")
    # memex's existing seeding: prior user/model text, as plain events.
    await fresh.append_event(s2, Event(
        invocation_id="seed", author="user",
        content=types.Content(role="user", parts=[types.Part(text="fix n-1")])))
    # ...plus the pending-confirmation events, replayed verbatim.
    for d in wire:
        await fresh.append_event(s2, Event.model_validate(d))

    llm2 = ScriptedLlm(turns=[txt("Done.")])
    try:
        async for _ in Runner(agent=agent(llm2), app_name="memex", session_service=fresh).run_async(
                user_id="u", session_id=s2.id,
                new_message=types.Content(role="user", parts=[types.Part(
                    function_response=types.FunctionResponse(
                        id=confirm.id, name="adk_request_confirmation",
                        response={"confirmed": True, "payload": None}))])):
            pass
    except ValueError as exc:
        print("RESULT resume raised:", type(exc).__name__, str(exc)[:140])
    print("RESULT tool executed from minimal replay:", executed)


asyncio.run(main())
