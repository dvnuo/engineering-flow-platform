import importlib
import subprocess
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


def test_src_tool_surface_is_runtime_v2_opencode_only():
    import src

    names = set(src.get_tool_names())
    assert {"bash", "read", "write", "edit", "grep", "glob", "webfetch", "todowrite", "apply_patch"}.issubset(names)
    assert {
        "jira_get_issue",
        "github_get_pr",
        "confluence_get_page",
        "git_clone",
        "run_command",
        "list_dir",
    }.isdisjoint(names)


def test_gateway_entrypoints_do_not_reference_legacy_chat_loop_symbols():
    for relative in ("src/gateway/runtime_api.py", "src/gateway/server.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        for module_name in REMOVED_LEGACY_MODULES:
            assert module_name not in text
        assert "AgentCore" not in text
        assert "run_chat_execution" not in text


def test_production_runtime_paths_do_not_import_legacy_session_sources():
    command = [
        "rg",
        "-n",
        (
            "from src\\.sessions\\.manager import session_manager|"
            "import src\\.sessions\\.manager|"
            "from src\\.sessions\\.persistence|"
            "session_persistence"
        ),
        "src/gateway",
        "src/runtime",
        "src/agents",
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 1, result.stdout + result.stderr


def test_runtime_v2_chat_uses_gateway_facade_store_contract():
    text = (ROOT / "src/gateway/runtime_v2_chat.py").read_text(encoding="utf-8")
    assert "from src.efp_runtime.runtime import AgentRuntime, RuntimeConfig" in text
    assert "runtime = AgentRuntime(" in text
    assert "get_runtime_v2_session_store" in text
    assert "store=get_runtime_v2_session_store()" in text
    assert "get_runtime_v2_session_manager().record_runtime_result" in text


def test_src_init_does_not_aggregate_legacy_python_tools():
    text = (ROOT / "src/__init__.py").read_text(encoding="utf-8")
    for token in (
        "get_jira_tools",
        "get_github_tools",
        "get_confluence_tools",
        "get_bash_tools",
        "context_tools",
    ):
        assert token not in text
