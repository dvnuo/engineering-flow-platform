import asyncio
import json
from types import SimpleNamespace

import pytest

from src import ToolResult
from src.agents.core import Agent


class _Tracer:
    def start_execution(self, **kwargs):
        return "exec-ctx-1"

    def log_tool_call(self, *args, **kwargs):
        return None

    def complete_execution(self, *args, **kwargs):
        return None

    def get_events_for_ui(self, **kwargs):
        return []

    def log_thinking(self, *args, **kwargs):
        return None

    def log_skill_mode_entry(self, *args, **kwargs):
        return None

    def log_skill_mode_step(self, *args, **kwargs):
        return None

    def log_skill_mode_action(self, *args, **kwargs):
        return None

    def log_skill_mode_complete(self, *args, **kwargs):
        return None


class _SessionManager:
    def __init__(self):
        self.messages = []

    async def add_message(self, session_id, role, content, extra=None, wait_for_save=False):
        self.messages.append({"session_id": session_id, "role": role, "content": content, **(extra or {})})
        return f"m-{len(self.messages)}"

    async def get_history(self, session_id):
        return [m for m in self.messages if m.get("session_id") == session_id]

    async def set_active_skill_session(self, _session_id, _state):
        return None


@pytest.mark.asyncio
async def test_main_loop_tool_call_event_emits_session_and_agent_context(monkeypatch):
    from src.agents import core as core_mod

    captured_events = []

    def _capture_emit(event_type, data):
        captured_events.append((event_type, data))

    async def _no_fastlane(*args, **kwargs):
        return None

    async def _fake_execute_tool(**kwargs):
        return ToolResult(True, "ok")

    responses = [
        {"content": "", "function_calls": [{"call_id": "c1", "name": "allowed_tool", "arguments": "{}"}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]

    async def _fake_responses(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("src.skills.get_tracer", lambda: _Tracer())
    monkeypatch.setattr(core_mod, "session_manager", _SessionManager())
    monkeypatch.setattr("src.agents.fastlane.process_fastlane_command", _no_fastlane)
    monkeypatch.setattr(core_mod, "memory_system", SimpleNamespace(build_context_with_search=lambda **kwargs: ""))
    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=_fake_responses, default_provider="openai"))
    monkeypatch.setattr(core_mod, "_execute_tool_via_runtime_bus", _fake_execute_tool)
    monkeypatch.setattr("src.gateway.event_bus.emit_agent_event_sync", _capture_emit)
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, load_skills=lambda: None, match_skill=lambda *_: []),
    )

    agent = Agent.__new__(Agent)
    agent.system_prompt = "base"
    agent.tools = [{"function": {"name": "allowed_tool", "description": "ok"}}]
    agent.include_memory = False
    agent.think_level = SimpleNamespace(value="off")
    agent.model = None
    agent.memory_update_manager = None
    agent.agent_id = "agent-ctx"
    agent.agent_name = "AgentCtx"

    result = await agent.process("run tool", session_id="s1")
    assert result["response"] == "done"

    tool_call_event = next(data for event_type, data in captured_events if event_type == "tool_call" and data.get("status") == "executing")
    assert tool_call_event["session_id"] == "s1"
    assert tool_call_event["agent_id"] == "agent-ctx"


@pytest.mark.asyncio
async def test_start_skill_mode_events_include_session_and_agent_context(monkeypatch):
    from src.agents import core as core_mod

    captured_events = []

    class _Bus:
        def emit_sync(self, event_type, data):
            captured_events.append((event_type, data))

    async def _fake_generate_initial_skill_plan(skill, message, model=None):
        return "goal", ["step-1"], {}

    async def _fake_continue_skill_mode(**kwargs):
        return {"response": "continued", "usage": {}, "events": [], "user_message_id": kwargs["user_message_id"]}

    monkeypatch.setattr("src.skills.get_tracer", lambda: _Tracer())
    monkeypatch.setattr(core_mod, "session_manager", _SessionManager())
    monkeypatch.setattr(core_mod, "generate_initial_skill_plan", _fake_generate_initial_skill_plan)
    monkeypatch.setattr("src.gateway.event_bus.event_bus", _Bus())

    agent = Agent.__new__(Agent)
    agent.model = None
    agent.agent_id = "agent-ctx"
    agent.agent_name = "AgentCtx"
    agent._continue_skill_mode = _fake_continue_skill_mode

    skill = SimpleNamespace(name="skill-a", path="")

    result = await agent._start_skill_mode(
        message="do thing",
        session_id="s1",
        user_message_id="u1",
        skill=skill,
    )

    assert result["response"] == "continued"
    first_event = captured_events[0]
    assert first_event[0] == "skill_mode_start"
    assert first_event[1]["session_id"] == "s1"
    assert first_event[1]["agent_id"] == "agent-ctx"


@pytest.mark.asyncio
async def test_filtered_event_bus_listener_receives_skill_event_with_session_context(monkeypatch):
    from src.agents import core as core_mod
    from src.gateway.event_bus import event_bus

    async def _fake_generate_initial_skill_plan(skill, message, model=None):
        return "goal", ["step-1"], {}

    async def _fake_continue_skill_mode(**kwargs):
        return {"response": "continued", "usage": {}, "events": [], "user_message_id": kwargs["user_message_id"]}

    monkeypatch.setattr("src.skills.get_tracer", lambda: _Tracer())
    monkeypatch.setattr(core_mod, "session_manager", _SessionManager())
    monkeypatch.setattr(core_mod, "generate_initial_skill_plan", _fake_generate_initial_skill_plan)

    queue = asyncio.Queue()
    await event_bus.add_listener(queue, filters={"session_id": "s1"})

    agent = Agent.__new__(Agent)
    agent.model = None
    agent.agent_id = "agent-ctx"
    agent.agent_name = "AgentCtx"
    agent._continue_skill_mode = _fake_continue_skill_mode

    skill = SimpleNamespace(name="skill-a", path="")

    try:
        await agent._start_skill_mode(
            message="do thing",
            session_id="s1",
            user_message_id="u1",
            skill=skill,
        )

        raw_event = await asyncio.wait_for(queue.get(), timeout=1.0)
        event = json.loads(raw_event)
        assert event["type"] == "skill_mode_start"
        assert event["data"]["session_id"] == "s1"
        assert event["data"]["agent_id"] == "agent-ctx"
    finally:
        await event_bus.remove_listener(queue)
