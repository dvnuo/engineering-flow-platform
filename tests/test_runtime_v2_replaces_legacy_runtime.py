import importlib
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_removed_bash_tools_package_is_absent_and_not_importable():
    assert not (ROOT / "src/bash_tools").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.bash_tools")


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
    for relative in ("src/gateway/webchat.py", "src/gateway/server.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "src.agents.core" not in text
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


def test_diff_contains_replacement_and_deletion_rows():
    committed = subprocess.check_output(
        ["git", "diff", "--name-status", "origin/master...HEAD"],
        cwd=ROOT,
        text=True,
    )
    staged = subprocess.check_output(
        ["git", "diff", "--name-status", "--cached"],
        cwd=ROOT,
        text=True,
    )
    unstaged = subprocess.check_output(
        ["git", "diff", "--name-status"],
        cwd=ROOT,
        text=True,
    )
    lines = [*committed.splitlines(), *staged.splitlines(), *unstaged.splitlines()]
    assert any(line.startswith("M\tsrc/gateway/webchat.py") for line in lines)
    assert any(line.startswith("M\tsrc/gateway/server.py") for line in lines)
    assert any(line.startswith("M\tsrc/__init__.py") for line in lines)
    assert any(line.startswith("D\tsrc/bash_tools/") for line in lines)
    assert any(not line.startswith("A\t") for line in lines)
