import pytest

from src import ToolResult
from src.agents import executor as executor_module


@pytest.mark.asyncio
async def test_execute_tool_by_name_denied_by_llm_tools_policy(monkeypatch):
    original_llm = dict(executor_module.config.llm or {})
    monkeypatch.setitem(executor_module.config._config, "llm", {"tools": ["git_clone"]})

    called = {"value": False}

    async def _fake_execute_tool(name, **kwargs):
        called["value"] = True
        return ToolResult(success=True, content="should not run")

    monkeypatch.setattr(executor_module, "execute_tool", _fake_execute_tool)

    result = await executor_module.execute_tool_by_name("jira_get_issue", issue_key="EFP-1")

    assert result.success is False
    assert "disabled by llm.tools policy" in (result.error or "")
    assert called["value"] is False
    monkeypatch.setitem(executor_module.config._config, "llm", original_llm)


@pytest.mark.asyncio
async def test_execute_tool_by_name_allowed_keeps_existing_behavior(monkeypatch):
    original_llm = dict(executor_module.config.llm or {})
    monkeypatch.setitem(executor_module.config._config, "llm", {"tools": ["git_clone"]})

    async def _fake_execute_tool(name, **kwargs):
        return ToolResult(success=True, content=f"ok:{name}:{kwargs.get('repo')}")

    monkeypatch.setattr(executor_module, "execute_tool", _fake_execute_tool)

    result = await executor_module.execute_tool_by_name("git_clone", repo="org/repo")

    assert result.success is True
    assert result.content == "ok:git_clone:org/repo"
    monkeypatch.setitem(executor_module.config._config, "llm", original_llm)
