import pytest
from types import SimpleNamespace

from src import ToolResult
from src.agents.core import Agent
from src.skills.registry import SkillRegistry
from src.skills.runtime import build_skill_prompt_blocks, build_skill_runtime_config


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
    monkeypatch.setattr("src.agents.core.process_fastlane_command", _no_fastlane, raising=False)
    monkeypatch.setattr("src.agents.core.memory_system", SimpleNamespace(build_context_with_search=lambda **kwargs: ""))
    monkeypatch.setattr("src.agents.core.send_event", lambda *a, **k: None, raising=False)
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


def test_structured_prompt_blocks_are_compact():
    skill = SimpleNamespace(
        name="compact",
        description="desc",
        tools=["a"],
        task_tools=["b"],
        strategy=["step1"],
        body="line1\nline2",
        references=["/tmp/ref-a.md"],
        model="",
        hooks=[],
        path="",
    )
    blocks = build_skill_prompt_blocks(skill)
    assert "Runtime policy" in blocks.system_rules
    assert "Skill: compact" in blocks.developer_instructions
    assert "ref-a.md" in blocks.references_summary


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
    monkeypatch.setattr("src.skills.skill_registry", SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s: build_skill_runtime_config(s)))

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
async def test_disallowed_tool_is_denied_and_not_executed(monkeypatch, base_agent):
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
    monkeypatch.setattr("src.skills.skill_registry", SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s: build_skill_runtime_config(s)))

    calls = {"execute": 0}

    async def fake_execute(name, **kwargs):
        calls["execute"] += 1
        return ToolResult(True, "tool ok")

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", fake_execute)

    responses = [
        {"content": "", "function_calls": [{"call_id": "1", "name": "blocked_tool", "arguments": "{}"}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]

    async def fake_responses(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))
    result = await agent.process("runtime test", session_id="s2")
    assert result["response"] == "done"
    assert calls["execute"] == 0


@pytest.mark.asyncio
async def test_task_capable_tool_uses_task_manager(monkeypatch, base_agent):
    agent, _ = base_agent
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
        hooks=[],
    )
    monkeypatch.setattr("src.skills.skill_registry", SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s: build_skill_runtime_config(s)))

    task_calls = {"n": 0}

    class _TaskRecord:
        def __init__(self, result):
            self.result = result

    async def fake_submit_tool_task(**kwargs):
        task_calls["n"] += 1
        result = await kwargs["coro_factory"]()
        return _TaskRecord(result)

    monkeypatch.setattr("src.agents.core.task_manager", SimpleNamespace(submit_tool_task=fake_submit_tool_task))
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
    result = await agent.process("runtime test", session_id="s3")
    assert result["response"] == "done"
    assert task_calls["n"] == 1
