"""Two more ADK 2.8.0 facts the memex tool-policy design turns on."""
import asyncio
from collections.abc import AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types
from pydantic import Field

executed, predicate_saw = [], []


def update_task(task_id: str, changes: dict) -> dict:
    executed.append(task_id)
    return {"ok": True}


def needs_confirmation(task_id: str, changes: dict) -> bool:
    """The policy hook: it sees the actual call arguments."""
    predicate_saw.append({"task_id": task_id, "changes": changes})
    return changes.get("status") == "dropped"   # only the lossy edit asks


class ScriptedLlm(BaseLlm):
    model: str = "scripted"
    turns: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse]:
        self.seen.append(llm_request)
        yield self.turns[min(len(self.seen) - 1, len(self.turns) - 1)]


def fc(name, args, i="fc-1"):
    return LlmResponse(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(id=i, name=name, args=args))]))


def txt(t):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=t)]))


async def run(agent, svc, session, message):
    runner = Runner(agent=agent, app_name="memex", session_service=svc)
    calls = []
    async for ev in runner.run_async(user_id="u", session_id=session.id, new_message=message):
        calls.extend(f for f in ev.get_function_calls() if f.name == "adk_request_confirmation")
    return calls


def build(llm):
    return LlmAgent(name="memex_chat", model=llm, instruction="test",
                    tools=[FunctionTool(update_task, require_confirmation=needs_confirmation)])


async def main():
    # --- FACT 5: the predicate sees the arguments, and gates per call --------
    svc = InMemorySessionService()
    s = await svc.create_session(app_name="memex", user_id="u")
    llm = ScriptedLlm(turns=[fc("update_task", {"task_id": "t-1", "changes": {"status": "done"}}), txt("ok")])
    got = await run(build(llm), svc, s, types.Content(role="user", parts=[types.Part(text="close t-1")]))
    print("FACT 5 predicate saw args:", predicate_saw)
    print("FACT 5 benign edit auto-ran (no confirmation):", executed == ["t-1"], "| confirmations:", len(got))

    executed.clear(); predicate_saw.clear()
    svc2 = InMemorySessionService()
    s2 = await svc2.create_session(app_name="memex", user_id="u")
    llm2 = ScriptedLlm(turns=[fc("update_task", {"task_id": "t-2", "changes": {"status": "dropped"}}), txt("ok")])
    got2 = await run(build(llm2), svc2, s2, types.Content(role="user", parts=[types.Part(text="drop t-2")]))
    print("FACT 5 lossy edit paused:", executed == [] and len(got2) == 1)

    # --- FACT 6: rejection ---------------------------------------------------
    llm2.turns, llm2.seen = [txt("Okay, I left it alone.")], []
    await run(build(llm2), svc2, s2, types.Content(role="user", parts=[types.Part(
        function_response=types.FunctionResponse(
            id=got2[0].id, name="adk_request_confirmation",
            response={"confirmed": False, "payload": None}))]))
    print("FACT 6 rejected tool never executed:", executed == [])

    # --- FACT 7: a text-only reseed cannot resume ---------------------------
    # memex rebuilds the ADK session each turn from stored TEXT events only
    # (memex/agent/chat.py). Reproduce that: fresh session, text history only.
    fresh = InMemorySessionService()
    s3 = await fresh.create_session(app_name="memex", user_id="u")
    llm3 = ScriptedLlm(turns=[txt("Okay!")])
    try:
        await run(build(llm3), fresh, s3, types.Content(role="user", parts=[types.Part(
            function_response=types.FunctionResponse(
                id=got2[0].id, name="adk_request_confirmation",
                response={"confirmed": True, "payload": None}))]))
        print("FACT 7 resumed against a text-only session:", executed != [])
    except ValueError as exc:
        print("FACT 7 resume against text-only session raised:", type(exc).__name__, str(exc)[:120])
    print("FACT 7 executed after text-only resume attempt:", executed)


asyncio.run(main())
