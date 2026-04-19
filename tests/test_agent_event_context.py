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

    async def get_active_skill_session(self, _session_id):
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
    from src.gateway import event_bus as event_bus_mod
    monkeypatch.setattr(event_bus_mod, "emit_agent_event_sync", _capture_emit)
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

    result = await agent.process("run tool", session_id="s1", request_id="req-runtime-events")
    assert result["response"] == "done"
    assert isinstance(result.get("runtime_events"), list)
    runtime_events = result["runtime_events"]
    assert any(event.get("request_id") == "req-runtime-events" for event in runtime_events)
    assert any((event.get("event_type") or event.get("type")) == "context_snapshot" for event in runtime_events)
    assert any((event.get("event_type") or event.get("type")) in {"tool_call", "tool_result"} for event in runtime_events)

    tool_call_event = next(data for event_type, data in captured_events if event_type == "tool_call" and data.get("status") == "executing")
    assert tool_call_event["session_id"] == "s1"
    assert tool_call_event["agent_id"] == "agent-ctx"
    assert tool_call_event["request_id"] == "req-runtime-events"


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
    from src.gateway import event_bus as event_bus_mod
    monkeypatch.setattr(event_bus_mod, "event_bus", _Bus())

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
        request_id="req-skill",
    )

    assert result["response"] == "continued"
    first_event = captured_events[0]
    assert first_event[0] == "skill_mode_start"
    assert first_event[1]["session_id"] == "s1"
    assert first_event[1]["agent_id"] == "agent-ctx"
    assert first_event[1]["request_id"] == "req-skill"


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
    await event_bus.add_listener(queue, filters={"session_id": "s1", "request_id": "req-skill"})

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
            request_id="req-skill",
        )

        raw_event = await asyncio.wait_for(queue.get(), timeout=1.0)
        event = json.loads(raw_event)
        assert event["type"] == "skill_mode_start"
        assert event["data"]["session_id"] == "s1"
        assert event["data"]["agent_id"] == "agent-ctx"
        assert event["data"]["request_id"] == "req-skill"
    finally:
        await event_bus.remove_listener(queue)


@pytest.mark.asyncio
async def test_skill_contract_active_event_includes_goal_and_allowed_tools_shape(monkeypatch):
    from src.agents import core as core_mod

    captured_events = []

    def _capture_emit(event_type, data):
        captured_events.append((event_type, data))

    async def _no_fastlane(*args, **kwargs):
        return None

    async def _fake_execute_tool(**kwargs):
        return ToolResult(True, "ok")

    responses = [
        {"content": "done", "function_calls": [], "usage": {}},
    ]

    async def _fake_responses(**kwargs):
        return responses.pop(0)

    skill = SimpleNamespace(name="skill-a", path="", deprecated=False)
    runtime_config = SimpleNamespace(
        skill_name="skill-a",
        allowed_tools=["allowed_tool"],
        allowed_tools_set={"allowed_tool"},
        tool_policy_declared=False,
        model_override=None,
        task_tools=[],
        hooks=[],
        workdir="",
        references=[],
        prompt_blocks=SimpleNamespace(developer_instructions=""),
    )

    monkeypatch.setattr("src.skills.get_tracer", lambda: _Tracer())
    monkeypatch.setattr(core_mod, "session_manager", _SessionManager())
    monkeypatch.setattr("src.agents.fastlane.process_fastlane_command", _no_fastlane)
    monkeypatch.setattr(core_mod, "memory_system", SimpleNamespace(build_context_with_search=lambda **kwargs: ""))
    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=_fake_responses, default_provider="openai"))
    monkeypatch.setattr(core_mod, "_execute_tool_via_runtime_bus", _fake_execute_tool)
    monkeypatch.setattr(core_mod, "get_effective_skill_runtime_prompt", lambda **kwargs: {"system_prompt": "base"})
    monkeypatch.setattr(core_mod, "resolve_prompt_execution_boundary", lambda _assembly: ("base", "append"))
    monkeypatch.setattr(core_mod, "get_skill_reference_attachment", lambda _runtime: None)
    monkeypatch.setattr(core_mod, "build_skill_runtime_event_payload", lambda **kwargs: {})
    from src.gateway import event_bus as event_bus_mod
    monkeypatch.setattr(event_bus_mod, "emit_agent_event_sync", _capture_emit)
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(
            _initialized=True,
            load_skills=lambda: None,
            match_skill=lambda *_: [skill],
            get_skill_runtime_config=lambda *_args, **_kwargs: runtime_config,
        ),
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

    await agent.process("use skill", session_id="s1", request_id="req-skill-contract")

    skill_contract_event = next(data for event_type, data in captured_events if event_type == "skill_contract_active")
    assert skill_contract_event.get("goal") or skill_contract_event.get("allowed_tools") is not None
    assert isinstance(skill_contract_event.get("allowed_tools"), list)


@pytest.mark.asyncio
async def test_fastlane_early_result_includes_request_id_and_runtime_event(monkeypatch):
    from src.agents import core as core_mod

    async def _fastlane(*args, **kwargs):
        return "Fastlane OK"

    monkeypatch.setattr(core_mod, "session_manager", _SessionManager())
    monkeypatch.setattr("src.agents.fastlane.process_fastlane_command", _fastlane)

    agent = Agent.__new__(Agent)
    agent.system_prompt = "base"
    agent.tools = []
    agent.include_memory = False
    agent.think_level = SimpleNamespace(value="off")
    agent.model = None
    agent.memory_update_manager = None
    agent.agent_id = "agent-ctx"
    agent.agent_name = "AgentCtx"

    collector = asyncio.Queue()
    result = await agent.process(
        "some fastlane command",
        session_id="s1",
        request_id="req-fastlane-live",
        stream_callback=collector,
    )
    assert result["request_id"] == "req-fastlane-live"
    assert isinstance(result["runtime_events"], list)
    assert result["runtime_events"][0]["request_id"] == "req-fastlane-live"
    assert result["runtime_events"][0]["event_type"] == "execution.completed"
    callback_event = json.loads(await asyncio.wait_for(collector.get(), timeout=1.0))
    assert callback_event["type"] == "execution.completed"
    assert callback_event["request_id"] == "req-fastlane-live"


@pytest.mark.asyncio
async def test_skill_not_found_early_result_marks_failed_runtime_event(monkeypatch):
    from src.agents import core as core_mod

    async def _no_fastlane(*args, **kwargs):
        return None

    monkeypatch.setattr(core_mod, "session_manager", _SessionManager())
    monkeypatch.setattr("src.agents.fastlane.process_fastlane_command", _no_fastlane)
    monkeypatch.setattr(core_mod, "parse_explicit_skill_switch_name", lambda _message: "missing-skill")
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(
            _initialized=True,
            load_skills=lambda: None,
            get_skill=lambda _name: None,
            match_skill=lambda *_: [],
        ),
    )

    agent = Agent.__new__(Agent)
    agent.system_prompt = "base"
    agent.tools = []
    agent.include_memory = False
    agent.think_level = SimpleNamespace(value="off")
    agent.model = None
    agent.memory_update_manager = None
    agent.agent_id = "agent-ctx"
    agent.agent_name = "AgentCtx"

    result = await agent.process("use missing skill", session_id="s1", request_id="req-missing")
    assert result["runtime_events"][0]["event_type"] == "execution.failed"
    assert result["runtime_events"][0]["request_id"] == "req-missing"


@pytest.mark.asyncio
async def test_context_snapshot_emits_compaction_planned_when_approaching_threshold(monkeypatch):
    from src.agents import core as core_mod

    captured_events = []

    async def _no_fastlane(*args, **kwargs):
        return None

    async def _fake_execute_tool(**kwargs):
        return ToolResult(True, "ok")

    responses = [
        {"content": "done", "function_calls": [], "usage": {}},
    ]

    async def _fake_responses(**kwargs):
        return responses.pop(0)

    async def _fake_prepare_progressive_messages(**kwargs):
        return kwargs["messages"], {
            "compaction_level": "none",
            "budget": {
                "next_compaction_action": "approaching_micro_compaction",
                "next_pruning_policy": "Approaching micro-compaction...",
            },
        }

    def _capture_emit(event_type, data):
        captured_events.append((event_type, data))

    monkeypatch.setattr("src.skills.get_tracer", lambda: _Tracer())
    monkeypatch.setattr(core_mod, "session_manager", _SessionManager())
    monkeypatch.setattr("src.agents.fastlane.process_fastlane_command", _no_fastlane)
    monkeypatch.setattr(core_mod, "memory_system", SimpleNamespace(build_context_with_search=lambda **kwargs: ""))
    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=_fake_responses, default_provider="openai"))
    monkeypatch.setattr(core_mod, "_execute_tool_via_runtime_bus", _fake_execute_tool)
    monkeypatch.setattr(core_mod, "prepare_progressive_messages", _fake_prepare_progressive_messages)
    from src.gateway import event_bus as event_bus_mod
    monkeypatch.setattr(event_bus_mod, "emit_agent_event_sync", _capture_emit)
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, load_skills=lambda: None, match_skill=lambda *_: []),
    )

    agent = Agent.__new__(Agent)
    agent.system_prompt = "base"
    agent.tools = []
    agent.include_memory = False
    agent.think_level = SimpleNamespace(value="off")
    agent.model = None
    agent.memory_update_manager = None
    agent.agent_id = "agent-ctx"
    agent.agent_name = "AgentCtx"

    await agent.process("run", session_id="s1", request_id="req-planned")
    planned_payload = next(data for event_type, data in captured_events if event_type == "context_compaction_planned")
    assert planned_payload.get("next_pruning_policy") or planned_payload.get("budget", {}).get("next_pruning_policy")
