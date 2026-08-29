"""Prove ADK 2.8.0's require_confirmation flow end to end, with a fake model.

Asserts, without any Vertex call:
  1. a require_confirmation tool does NOT execute on the first turn;
  2. the turn ends with an `adk_request_confirmation` long-running function call;
  3. replying with a ToolConfirmation(confirmed=True) FunctionResponse in a
     SECOND run_async executes the tool for real.
"""
import asyncio
import json
from collections.abc import AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types
from pydantic import Field

executed: list[dict] = []


def update_note(note_id: str, summary: str) -> dict:
    executed.append({"note_id": note_id, "summary": summary})
    return {"ok": True}


class ScriptedLlm(BaseLlm):
    """Returns a canned response per LLM turn, in order."""
    model: str = "scripted"
    turns: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse]:
        self.seen.append(llm_request)
        idx = min(len(self.seen) - 1, len(self.turns) - 1)
        yield self.turns[idx]


def fc_response(name, args):
    return LlmResponse(content=types.Content(
        role="model",
        parts=[types.Part(function_call=types.FunctionCall(id="fc-1", name=name, args=args))],
    ))


def text_response(text):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


async def main():
    llm = ScriptedLlm(turns=[
        fc_response("update_note", {"note_id": "n-1", "summary": "new summary"}),
        text_response("Done — I updated the note."),
    ])
    agent = LlmAgent(
        name="memex_chat",
        model=llm,
        instruction="you are a test",
        tools=[FunctionTool(update_note, require_confirmation=True)],
    )
    svc = InMemorySessionService()
    runner = Runner(agent=agent, app_name="memex", session_service=svc)
    session = await svc.create_session(app_name="memex", user_id="u")

    # --- turn 1: the model asks to mutate -----------------------------------
    confirm_call = None
    events1 = []
    async for ev in runner.run_async(
        user_id="u", session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="fix note n-1")]),
    ):
        events1.append(ev)
        for fc in ev.get_function_calls():
            if fc.name == "adk_request_confirmation":
                confirm_call = fc

    print("FACT 1 tool executed on turn 1:", executed)
    print("FACT 2 confirmation requested:", confirm_call is not None)
    if confirm_call:
        print("FACT 2 confirmation call id:", confirm_call.id)
        print("FACT 2 long_running ids seen:",
              [sorted(e.long_running_tool_ids) for e in events1 if e.long_running_tool_ids])
        print("FACT 2 original call preserved:",
              json.dumps(confirm_call.args.get("originalFunctionCall"), sort_keys=True))

    # --- turn 2: the human answers ------------------------------------------
    llm.turns = [text_response("Done — I updated the note.")]
    llm.seen = []
    reply = types.Content(role="user", parts=[types.Part(
        function_response=types.FunctionResponse(
            id=confirm_call.id,
            name="adk_request_confirmation",
            response={"confirmed": True, "payload": None},
        )
    )])
    async for ev in runner.run_async(user_id="u", session_id=session.id, new_message=reply):
        pass

    print("FACT 3 tool executed after confirming:", executed)

    stored = await svc.get_session(app_name="memex", user_id="u", session_id=session.id)
    print("FACT 4 stored session event count:", len(stored.events))
    print("FACT 4 events carrying function calls/responses:",
          sum(1 for e in stored.events if e.get_function_calls() or e.get_function_responses()))


asyncio.run(main())
