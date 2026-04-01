import pytest
from types import SimpleNamespace

from src import ToolResult
from src.agents.core import Agent
from src.agents.skill_runtime import build_skill_tool_denied_result
from src.skills.registry import SkillRegistry
from src.skills.runtime import (
    attach_skill_references,
    assemble_effective_prompt,
    build_skill_runtime_config,
)


class _Tracer:
    def start_execution(self, **kwargs):
        return "exec-1"

    def log_tool_call(self, *args, **kwargs):
        return None

    def complete_execution(self, *args, **kwargs):
        return None

    def get_events_for_ui(self, **kwargs):
        return []

    def log_thinking(self, *args, **kwargs):
        return None


class _SessionManager:
    def __init__(self):
        self.messages = []

    async def add_message(self, session_id, role, content, extra=None, wait_for_save=False):
        self.messages.append({"role": role, "content": content, **(extra or {})})
        return f"msg-{len(self.messages)}"

    async def get_history(self, session_id):
        return list(self.messages)


@pytest.fixture
def base_agent(monkeypatch):
    agent = Agent.__new__(Agent)
    agent.system_prompt = "BASE"
    agent.tools = [
        {"function": {"name": "allowed_tool", "description": "ok"}},
        {"function": {"name": "blocked_tool", "description": "no"}},
    ]
    agent.include_memory = False
    agent.think_level = SimpleNamespace(value="off")
    agent.model = None
    agent.memory_update_manager = None

    sess = _SessionManager()
    monkeypatch.setattr("src.agents.core.session_manager", sess)
    monkeypatch.setattr("src.skills.get_tracer", lambda: _Tracer())

    async def _no_fastlane(*args, **kwargs):
        return None

    monkeypatch.setattr("src.agents.fastlane.process_fastlane_command", _no_fastlane)
    monkeypatch.setattr("src.agents.core.memory_system", SimpleNamespace(build_context_with_search=lambda **kwargs: ""))
    return agent, sess


def test_skill_registry_parses_frontmatter_body_and_defaults(tmp_path):
    skill_file = tmp_path / "skill.md"
    skill_file.write_text(
        """---
name: runtime-test
description: runtime test skill
trigger:
  - runtime test
tools:
  - allowed_tool
---
# Body Title
Use compact instructions.
""",
        encoding="utf-8",
    )
    registry = SkillRegistry(project_skills_dir=str(tmp_path), user_skills_dir=str(tmp_path / "none"))
    skill = registry._load_skill_file(skill_file)
    assert skill is not None
    assert "Body Title" in skill.body
    assert skill.when_to_use == []
    assert skill.model == ""
    assert skill.hooks == []
    assert skill.task_tools == []


def test_prompt_layer_assembly_and_reference_context():
    skill = SimpleNamespace(
        name="compact",
        description="desc",
        tools=["a"],
        task_tools=["b"],
        strategy=["step1"],
        body="line1\nline2",
        references=["/tmp/ref-a.md", "/tmp/ref-b.md"],
        model="",
        hooks=[],
        path="",
    )
    runtime_config = build_skill_runtime_config(skill)
    assembly = assemble_effective_prompt("BASE", runtime_config)

    assert "Runtime policy" in assembly.system_rules_text
    assert "Skill: compact" in assembly.developer_instructions_text
    assert "Available references:" in assembly.reference_context_text
    assert "ref-a.md" in assembly.reference_context_text
    assert "line1" not in assembly.reference_context_text
    assert assembly.serialized_system_prompt.count("Skill Developer Instructions") == 1

    attachment = attach_skill_references(runtime_config)
    assert attachment.references == ["/tmp/ref-a.md", "/tmp/ref-b.md"]
    assert "Available references:" in attachment.context_text


def test_build_skill_tool_denied_result_contains_policy():
    runtime_config = build_skill_runtime_config(
        SimpleNamespace(
            name="demo",
            description="demo",
            tools=["allowed_tool"],
            task_tools=[],
            strategy=[],
            body="",
            references=[],
            model="",
            hooks=[],
            path="",
        )
    )
    denied = build_skill_tool_denied_result(runtime_config, "blocked_tool")
    assert denied.success is False
    assert "blocked_tool" in str(denied)
    assert "allowed_tool" in str(denied)


@pytest.mark.asyncio
async def test_matched_skill_does_not_route_to_legacy_skill_mode(monkeypatch, base_agent):
    agent, _ = base_agent
    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=[],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s: build_skill_runtime_config(s)),
    )

    async def fake_responses(**kwargs):
        return {"content": "done", "function_calls": [], "usage": {}}

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))

    async def fail_start(*args, **kwargs):
        raise AssertionError("legacy _start_skill_mode should not be called")

    async def fail_continue(*args, **kwargs):
        raise AssertionError("legacy _continue_skill_mode should not be called")

    monkeypatch.setattr(agent, "_start_skill_mode", fail_start)
    monkeypatch.setattr(agent, "_continue_skill_mode", fail_continue)

    result = await agent.process("runtime test", session_id="s1")
    assert result["response"] == "done"


@pytest.mark.asyncio
async def test_disallowed_tool_is_denied_and_allowed_tool_executes(monkeypatch, base_agent):
    agent, _ = base_agent
    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=["/tmp/ref-a.md"],
        model="",
        hooks=[],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s: build_skill_runtime_config(s)),
    )

    calls = {"execute": 0, "names": []}

    async def fake_execute(name, **kwargs):
        calls["execute"] += 1
        calls["names"].append(name)
        return ToolResult(True, "tool ok")

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", fake_execute)

    responses = [
        {"content": "", "function_calls": [{"call_id": "1", "name": "blocked_tool", "arguments": "{}"}], "usage": {}},
        {"content": "", "function_calls": [{"call_id": "2", "name": "allowed_tool", "arguments": "{}"}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]

    async def fake_responses(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))
    result = await agent.process("runtime test", session_id="s2")
    assert result["response"] == "done"
    assert calls["execute"] == 1
    assert calls["names"] == ["allowed_tool"]


@pytest.mark.asyncio
async def test_hooks_and_task_path_emit_events(monkeypatch, base_agent):
    agent, _ = base_agent
    events = []

    def stream_callback(event_json: str):
        events.append(event_json)

    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=["allowed_tool"],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=["pre_tool", "post_tool"],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s: build_skill_runtime_config(s)),
    )

    async def _exec(*args, **kwargs):
        return ToolResult(True, "task ok")

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", _exec)

    responses = [
        {"content": "", "function_calls": [{"call_id": "1", "name": "allowed_tool", "arguments": "{}"}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]

    async def fake_responses(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))

    result = await agent.process("runtime test", session_id="s3", stream_callback=stream_callback)
    assert result["response"] == "done"
    assert any('"type": "skill_hook"' in e for e in events)
    assert any('"type": "task_started"' in e for e in events)
    assert any('"type": "task_finished"' in e for e in events)


@pytest.mark.asyncio
async def test_hook_failure_does_not_break_request(monkeypatch, base_agent):
    agent, _ = base_agent
    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=["pre_tool:tests.test_skill_runtime_refactor._failing_hook"],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s: build_skill_runtime_config(s)),
    )

    async def _exec(*args, **kwargs):
        return ToolResult(True, "task ok")

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", _exec)

    responses = [
        {"content": "", "function_calls": [{"call_id": "1", "name": "allowed_tool", "arguments": "{}"}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]

    async def fake_responses(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))
    result = await agent.process("runtime test", session_id="s-hook-fail")
    assert result["response"] == "done"


@pytest.mark.asyncio
async def test_non_skill_request_unaffected(monkeypatch, base_agent):
    agent, _ = base_agent
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [], get_skill_runtime_config=lambda s: None),
    )

    async def fake_responses(**kwargs):
        return {"content": "plain response", "function_calls": [], "usage": {}}

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))
    result = await agent.process("hello", session_id="s4")
    assert result["response"] == "plain response"


def _failing_hook(context):
    raise RuntimeError("hook failure for testing")
