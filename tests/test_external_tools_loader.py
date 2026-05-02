import importlib
import textwrap

import pytest


@pytest.fixture
def src_module():
    import src

    return src


def _create_fake_tools_repo(base_dir, descriptors_expr: str, runner_body: str):
    pkg_dir = base_dir / "python" / "efp_tools"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "contracts.py").write_text("", encoding="utf-8")
    (pkg_dir / "registry.py").write_text(
        f"""DESCRIPTORS = {descriptors_expr}

class Registry:
    def list_descriptors(self):
        return DESCRIPTORS

def load_registry(tools_dir):
    return Registry()
""",
        encoding="utf-8",
    )
    (pkg_dir / "runner.py").write_text(runner_body, encoding="utf-8")


def _reload_external_loader():
    import src.runtime.external_tools as ext

    importlib.reload(ext)
    ext.clear_external_tools_cache()
    return ext


def test_missing_tools_dir_falls_back_to_legacy(monkeypatch, tmp_path, src_module):
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tmp_path / "does-not-exist"))
    ext = _reload_external_loader()
    schemas = src_module.get_tools_schema()
    assert schemas
    assert ext.load_external_tools_state().available is False


def test_schema_merge_external_overrides_and_disabled(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_fake_tools_repo(
        repo,
        descriptors_expr="""[
            {"name": "run_command", "enabled": True, "runtime_compat": ["native"], "metadata": {}, "schema": {"type":"function", "function": {"name":"run_command", "description":"external run command", "parameters":{"type":"object","properties":{"cmd":{"type":"string"}}}}}},
            {"name": "git_status", "enabled": False, "runtime_compat": ["native"], "metadata": {}},
            {"name": "hidden_tool", "enabled": True, "runtime_compat": ["native"], "metadata": {"model_facing": False}},
        ]""",
        runner_body="async def execute_tool(name, kwargs, context=None):\n    return {'success': True, 'content': 'ok'}\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    _reload_external_loader()

    schemas = src_module.get_tools_schema()
    by_name = {(s.get("function", {}) or {}).get("name") or s.get("name"): s for s in schemas}
    assert "run_command" in by_name
    assert "git_status" not in by_name
    assert "hidden_tool" not in by_name
    assert by_name["run_command"]["function"]["description"] == "external run command"


@pytest.mark.asyncio
async def test_execute_external_priority_and_context(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_fake_tools_repo(
        repo,
        descriptors_expr='[{"name": "run_command", "enabled": True, "runtime_compat": ["native"], "metadata": {}}]',
        runner_body=textwrap.dedent(
            """
            async def execute_tool(name, kwargs, context=None):
                assert context["runtime_type"] == "native"
                assert context["session_id"] == "sess-1"
                return {"success": True, "content": f"external:{name}:{kwargs.get('cmd')}"}
            """
        ),
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    _reload_external_loader()

    result = await src_module.execute_tool("run_command", cmd="echo hi", _session_id="sess-1")
    assert result.success is True
    assert "external:run_command:echo hi" in result.content


@pytest.mark.asyncio
async def test_disabled_external_blocks_legacy_and_exec_alias(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_fake_tools_repo(
        repo,
        descriptors_expr='[{"name": "run_command", "enabled": False, "runtime_compat": ["native"], "metadata": {}}]',
        runner_body="async def execute_tool(name, kwargs, context=None):\n    return {'success': True, 'content': 'should-not-run'}\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    _reload_external_loader()

    result = await src_module.execute_tool("run_command", cmd="echo hi")
    assert result.success is False
    assert "disabled" in (result.error or "").lower()

    alias_result = await src_module.execute_tool("exec", command="ls")
    assert alias_result.success is False
    assert "disabled" in (alias_result.error or "").lower()


def test_strip_none_values_behavior(src_module):
    payload = src_module._strip_none_values({"a": None, "b": False, "c": 0, "d": ""})
    assert "a" not in payload
    assert payload["b"] is False
    assert payload["c"] == 0
    assert payload["d"] == ""


def test_import_failure_fallback_and_cache_switch(monkeypatch, tmp_path, src_module):
    bad_repo = tmp_path / "bad_repo"
    pkg = bad_repo / "python" / "efp_tools"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "registry.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    (pkg / "runner.py").write_text("", encoding="utf-8")

    monkeypatch.setenv("EFP_TOOLS_DIR", str(bad_repo))
    ext = _reload_external_loader()
    assert ext.load_external_tools_state().available is False
    assert src_module.get_tools_schema()

    good_repo = tmp_path / "good_repo"
    _create_fake_tools_repo(
        good_repo,
        descriptors_expr='[{"name": "run_command", "enabled": True, "runtime_compat": ["native"], "metadata": {}}]',
        runner_body="async def execute_tool(name, kwargs, context=None):\n    return {'success': True, 'content': 'ok'}\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(good_repo))
    ext.clear_external_tools_cache()
    state = ext.load_external_tools_state()
    assert state.available is True
