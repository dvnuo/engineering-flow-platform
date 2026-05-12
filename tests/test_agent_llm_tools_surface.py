from types import SimpleNamespace

import pytest

from src.agents.core import Agent


class _FakeTracer:
    def log_tool_call(self, *args, **kwargs):
        return None

    def log_skill_mode_action(self, *args, **kwargs):
        return None

    def log_skill_mode_step(self, *args, **kwargs):
        return None

    def log_skill_mode_complete(self, *args, **kwargs):
        return None

    def get_events_for_ui(self, **kwargs):
        return []


def test_agent_init_filters_global_tool_surface(monkeypatch):
    from src.agents import core as core_mod

    tool_catalog = [
        {"type": "function", "function": {"name": "git_clone", "description": "clone"}},
        {"type": "function", "function": {"name": "jira_get_issue", "description": "jira"}},
        {"type": "function", "function": {"name": "read", "description": "read"}},
    ]

    monkeypatch.setattr(core_mod, "get_tools_schemas", lambda: tool_catalog)
    monkeypatch.setitem(core_mod.config._config, "llm", {"tools": ["jira_*", "git_clone"]})
    monkeypatch.setattr(
        core_mod,
        "memory_system",
        SimpleNamespace(workspace="/tmp", build_system_prompt=lambda include_memory: ""),
    )

    agent = Agent(session_id="surface-test")

    assert [t["function"]["name"] for t in agent.tools] == ["git_clone", "jira_get_issue"]
    assert agent.allowed_tool_names == {"git_clone", "jira_get_issue"}


@pytest.mark.asyncio
async def test_skill_mode_tool_names_intersect_with_global_surface(monkeypatch):
    from src.agents import core as core_mod

    monkeypatch.setattr(core_mod, "get_tools_schemas", lambda: (_ for _ in ()).throw(AssertionError("must not fetch full catalog")))
    monkeypatch.setattr("src.skills.get_tracer", lambda: _FakeTracer())

    async def _fake_add_message(*args, **kwargs):
        return "m1"

    async def _fake_set_active(*args, **kwargs):
        return None

    monkeypatch.setattr(core_mod, "session_manager", SimpleNamespace(add_message=_fake_add_message, set_active_skill_session=_fake_set_active))

    captured = []

    async def _fake_responses(**kwargs):
        captured.append(kwargs.get("tools") or [])
        return {"content": "", "function_calls": [], "usage": {}}

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=_fake_responses))

    agent = Agent.__new__(Agent)
    agent.model = None
    agent.agent_id = None
    agent.agent_name = None
    agent.tools = [
        {"type": "function", "function": {"name": "git_clone", "description": "clone"}},
        {"type": "function", "function": {"name": "jira_get_issue", "description": "jira"}},
    ]
    agent.allowed_tool_names = {"git_clone", "jira_get_issue"}

    skill = SimpleNamespace(name="demo", description="demo", path="", tools=["git_clone", "read"], strategy=[])
    skill_state = {"skill_name": "demo", "original_user_request": "run", "completed_steps": [], "artifacts": {}}

    await agent._continue_skill_mode(
        message="run",
        session_id="s1",
        user_message_id="u1",
        skill_state=skill_state,
        skill=skill,
    )

    assert any([item["function"]["name"] for item in tools] == ["git_clone"] for tools in captured)


@pytest.mark.asyncio
async def test_skill_mode_tool_schema_list_intersect_with_global_surface(monkeypatch):
    from src.agents import core as core_mod

    monkeypatch.setattr(core_mod, "get_tools_schemas", lambda: (_ for _ in ()).throw(AssertionError("must not fetch full catalog")))
    monkeypatch.setattr("src.skills.get_tracer", lambda: _FakeTracer())

    async def _fake_add_message(*args, **kwargs):
        return "m1"

    async def _fake_set_active(*args, **kwargs):
        return None

    monkeypatch.setattr(core_mod, "session_manager", SimpleNamespace(add_message=_fake_add_message, set_active_skill_session=_fake_set_active))

    captured = []

    async def _fake_responses(**kwargs):
        captured.append(kwargs.get("tools") or [])
        return {"content": "", "function_calls": [], "usage": {}}

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=_fake_responses))

    agent = Agent.__new__(Agent)
    agent.model = None
    agent.agent_id = None
    agent.agent_name = None
    agent.tools = [
        {"type": "function", "function": {"name": "git_clone", "description": "clone"}},
        {"type": "function", "function": {"name": "jira_get_issue", "description": "jira"}},
    ]
    agent.allowed_tool_names = {"git_clone"}

    skill = SimpleNamespace(
        name="demo",
        description="demo",
        path="",
        tools=[
            {"type": "function", "function": {"name": "git_clone", "description": "clone"}},
            {"type": "function", "function": {"name": "jira_get_issue", "description": "jira"}},
        ],
        strategy=[],
    )
    skill_state = {"skill_name": "demo", "original_user_request": "run", "completed_steps": [], "artifacts": {}}

    await agent._continue_skill_mode(
        message="run",
        session_id="s1",
        user_message_id="u1",
        skill_state=skill_state,
        skill=skill,
    )

    assert any([item["function"]["name"] for item in tools] == ["git_clone"] for tools in captured)

def test_removed_external_tools_env_does_not_add_tools(monkeypatch, tmp_path):
    from src.agents import core as core_mod
    tools_dir = tmp_path / "tools_repo"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "manifest.yaml").write_text("name: context_echo\n", encoding="utf-8")
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    monkeypatch.setenv("EFP_EXTERNAL_TOOLS_ENABLED", "true")
    monkeypatch.setenv("EFP_EXTERNAL_TOOLS_STRICT", "true")
    monkeypatch.setitem(core_mod.config._config, "llm", {"tools": ["context_echo"]})
    monkeypatch.setattr(core_mod, "memory_system", SimpleNamespace(workspace="/tmp", build_system_prompt=lambda include_memory: ""))
    agent = Agent(session_id="external-surface-test")
    names = [t["function"]["name"] for t in agent.tools]
    assert "context_echo" not in names
    assert "context_echo" not in agent.allowed_tool_names


def test_src_tools_external_module_removed():
    import importlib
    import pytest
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.tools_external")
