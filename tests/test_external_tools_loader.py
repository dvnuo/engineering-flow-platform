from __future__ import annotations

import importlib
import textwrap
from pathlib import Path

import pytest


def _reload_external_loader():
    import src.runtime.external_tools as ext
    ext = importlib.reload(ext)
    ext.clear_external_tools_cache()
    return ext


def _write_tools_repo(repo: Path, *, enabled: bool = True, runtime_compat: str = "[native, opencode]") -> None:
    (repo / "manifest.yaml").write_text("version: 1\n", encoding="utf-8")
    p = repo / "tools" / "context"
    p.mkdir(parents=True, exist_ok=True)
    (p / "context_echo.yaml").write_text(textwrap.dedent(f"""
    tool_id: efp.tool.context.echo
    name: context_echo
    description: Echo context
    python_entrypoint: test_tools.echo:execute
    input_schema:
      type: object
      properties:
        text:
          type: string
    output_schema:
      type: object
    runtime_compat: {runtime_compat}
    policy_tags: [context, read_only]
    domain: context
    mutation: false
    risk_level: low
    enabled: {str(enabled).lower()}
    metadata:
      model_facing: true
    """), encoding="utf-8")
    mod = repo / "python" / "test_tools"
    mod.mkdir(parents=True, exist_ok=True)
    (mod / "__init__.py").write_text("", encoding="utf-8")
    (mod / "echo.py").write_text("async def execute(text='', **kwargs):\n    return {'success': True, 'content': text}\n", encoding="utf-8")


def test_missing_tools_dir_uses_empty_external_registry_and_legacy_surface_is_non_strict_only(monkeypatch, tmp_path):
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("EFP_EXTERNAL_TOOLS_STRICT", "false")
    import src
    ext = _reload_external_loader()
    state = ext.load_external_tools_state()
    assert state.available is True
    assert ext.get_external_tool_schemas("native") == []
    assert ("run_command" in src.get_tool_names()) or ("git_status" in src.get_tool_names())


def test_resolve_external_tools_dir_defaults_to_app_tools_when_env_unset(monkeypatch):
    from src.tools_external.manifest_loader import resolve_external_tools_dir

    monkeypatch.delenv("EFP_TOOLS_DIR", raising=False)
    monkeypatch.delenv("EFP_EXTERNAL_TOOLS_TEST_FIXTURE", raising=False)
    monkeypatch.delenv("EFP_TOOLS_FIXTURE_DIR", raising=False)
    assert resolve_external_tools_dir() == Path("/app/tools")


def test_legacy_tool_repo_envs_are_ignored_for_tools_dir_resolution(monkeypatch, tmp_path):
    from src.tools_external.manifest_loader import resolve_external_tools_dir

    explicit = tmp_path / "local-tools"
    monkeypatch.setenv("EFP_TOOLS_DIR", str(explicit))
    monkeypatch.setenv("DEFAULT_TOOL_REPO_URL", "https://example.com/tools.git")
    monkeypatch.setenv("DEFAULT_TOOL_BRANCH", "main")
    monkeypatch.setenv("TOOL_REPO_URL", "https://example.com/legacy-tools.git")
    monkeypatch.setenv("TOOL_BRANCH", "feature/tools")
    assert resolve_external_tools_dir() == explicit


def test_load_tool_descriptors_empty_dir_returns_empty_list(monkeypatch, tmp_path):
    from src.tools_external.manifest_loader import load_tool_descriptors

    empty_tools_dir = tmp_path / "empty-tools"
    empty_tools_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(empty_tools_dir))
    assert load_tool_descriptors() == []


def test_runtime_external_tools_wrapper_imports_contracts_descriptor_schema(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_tools_repo(repo)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    ext = _reload_external_loader()
    schemas = ext.get_external_tool_schemas("opencode")
    assert isinstance(schemas, list)


@pytest.mark.asyncio
async def test_runtime_external_tools_wrapper_unknown_execute_returns_none(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_tools_repo(repo)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    ext = _reload_external_loader()
    assert await ext.execute_external_tool("missing_tool", {}, runtime_type="native") is None


@pytest.mark.asyncio
async def test_runtime_external_tools_wrapper_disabled_descriptor_returns_disabled_error(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_tools_repo(repo, enabled=False)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    ext = _reload_external_loader()
    result = await ext.execute_external_tool("context_echo", {}, runtime_type="native")
    assert result and result["success"] is False
    assert "disabled" in (result["error"] or "")


@pytest.mark.asyncio
async def test_runtime_external_tools_wrapper_runtime_incompatible_returns_error(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_tools_repo(repo, runtime_compat="[opencode]")
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    ext = _reload_external_loader()
    result = await ext.execute_external_tool("context_echo", {}, runtime_type="native")
    assert result and result["success"] is False
    assert "runtime_incompatible" in (result["error"] or "")
