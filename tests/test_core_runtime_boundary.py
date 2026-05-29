import asyncio
import importlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

REMOVED_LEGACY_MODULES = {
    "src.agents.core": ROOT / "src/agents/core.py",
    "src.agents.executor": ROOT / "src/agents/executor.py",
    "src.agents.llm": ROOT / "src/agents/llm.py",
    "src.bash_tools": ROOT / "src/bash_tools",
    "src.context_tools": ROOT / "src/context_tools.py",
}


def test_removed_legacy_runtime_modules_are_absent_and_not_importable():
    for module_name, module_path in REMOVED_LEGACY_MODULES.items():
        assert not module_path.exists(), module_name
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_name)


def test_agents_package_does_not_export_legacy_runtime_shims():
    agents_pkg = importlib.import_module("src.agents")

    for exported_name in ("core", "executor", "llm"):
        assert exported_name not in getattr(agents_pkg, "__all__", [])
        assert not hasattr(agents_pkg, exported_name)


def test_gateway_chat_entrypoint_uses_runtime_v2_agent_runtime():
    source = (ROOT / "src/gateway/runtime_v2_chat.py").read_text(encoding="utf-8")

    assert "from src.efp_runtime.runtime import AgentRuntime, RuntimeConfig" in source
    assert "runtime = AgentRuntime(" in source
    assert "store=get_runtime_v2_session_store()" in source
    assert "get_runtime_v2_session_manager().record_runtime_result" in source


def test_gateway_entrypoints_do_not_import_removed_legacy_runtime_modules():
    for relative in ("src/gateway/webchat.py", "src/gateway/server.py", "src/gateway/runtime_v2_chat.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        for module_name in REMOVED_LEGACY_MODULES:
            assert module_name not in source
        assert "run_chat_execution" not in source
        assert "AgentCore" not in source


def test_runtime_v2_skill_execution_boundary_reports_legacy_skill_disabled():
    from src.runtime.execution_bus import run_skill_execution

    result = asyncio.run(run_skill_execution("demo_skill", input="hello"))

    assert result.success is False
    assert "Legacy Python skill execution is not available" in (result.error or "")
    assert result.data["runtime"] == "efp_runtime_v2"
