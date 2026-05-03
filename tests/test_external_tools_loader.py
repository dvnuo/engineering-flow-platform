import importlib
import os
import textwrap

import pytest


@pytest.fixture
def src_module():
    import src

    return src


def _reload_external_loader():
    import src.runtime.external_tools as ext

    importlib.reload(ext)
    ext.clear_external_tools_cache()
    return ext


def _create_tools_repo(base_dir, *, validate_errors="[]", runner_body="", dataclass_contract=True):
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "manifest.yaml").write_text("version: 1\n", encoding="utf-8")
    pkg_dir = base_dir / "python" / "efp_tools"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    if dataclass_contract:
        (pkg_dir / "contracts.py").write_text(
            textwrap.dedent(
                """
                from dataclasses import dataclass, field

                @dataclass
                class ToolDescriptor:
                    tool_id: str
                    name: str
                    opencode_name: str
                    description: str
                    domain: str
                    type: str
                    runtime_compat: list[str]
                    policy_tags: list[str]
                    requires_identity_binding: bool
                    mutation: bool
                    risk_level: str
                    python_entrypoint: str
                    input_schema: dict
                    output_schema: dict
                    metadata: dict = field(default_factory=dict)
                    enabled: bool = True
                """
            ),
            encoding="utf-8",
        )
        (pkg_dir / "registry.py").write_text(
            f"""
from efp_tools.contracts import ToolDescriptor

DESCRIPTORS = [
    ToolDescriptor('efp.tool.context.context_read_ref','context_read_ref','context_read_ref','Read context ref','context','read',['native'],['read'],False,False,'low','x',{{'type':'object','properties':{{'ref':{{'type':'string'}}}},'required':['ref']}},{{'type':'object'}},{{'model_facing': True}}, True),
    ToolDescriptor('efp.tool.git.git_status','git_status','git_status','Get git status','git','read',['native'],['read'],False,False,'low','x',{{'type':'object','properties':{{'workspace':{{'type':'string'}}}}}},{{'type':'object'}},{{'model_facing': True}}, True),
    ToolDescriptor('efp.tool.bash.run_command','run_command','run_command','Run command','bash','write',['native'],['exec'],False,True,'high','x',{{'type':'object','properties':{{'cmd':{{'type':'string'}}}}}},{{'type':'object'}},{{'model_facing': True}}, False),
]

class Registry:
    def list_descriptors(self, *, runtime_type=None, enabled_only=True, model_facing_only=True):
        items = list(DESCRIPTORS)
        if runtime_type:
            items = [d for d in items if runtime_type in (d.runtime_compat or [])]
        if enabled_only:
            items = [d for d in items if d.enabled]
        if model_facing_only:
            items = [d for d in items if (d.metadata or {{}}).get('model_facing', True) is not False]
        return items

    def list_all_descriptors(self):
        return list(DESCRIPTORS)

    def validate(self):
        return {validate_errors}

def load_registry(tools_dir):
    return Registry()
""",
            encoding="utf-8",
        )
    else:
        (pkg_dir / "contracts.py").write_text("", encoding="utf-8")
        (pkg_dir / "registry.py").write_text(
            "DESCRIPTORS = [{'name': 'run_command', 'enabled': True, 'runtime_compat': ['native'], 'metadata': {}, "
            "'schema': {'type': 'function', 'function': {'name': 'run_command','description':'external','parameters':{'type':'object'}}}}]\n"
            "class Registry:\n    def list_descriptors(self, **kwargs):\n        return DESCRIPTORS\n"
            "def load_registry(tools_dir):\n    return Registry()\n",
            encoding="utf-8",
        )
    if not runner_body:
        runner_body = "async def execute_tool_async(**kwargs):\n    return {'success': True, 'content': 'ok'}\n"
    (pkg_dir / "runner.py").write_text(runner_body, encoding="utf-8")


def test_missing_tools_dir_falls_back_to_legacy(monkeypatch, tmp_path, src_module):
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tmp_path / "does-not-exist"))
    ext = _reload_external_loader()
    assert src_module.get_tools_schema()
    assert ext.load_external_tools_state().available is False


def test_t04_dataclass_registry_semantics(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _create_tools_repo(repo)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    ext = _reload_external_loader()

    schemas = ext.get_external_tool_schemas("native")
    names = {(s.get("function", {}) or {}).get("name") for s in schemas}
    assert "context_read_ref" in names
    assert "git_status" in names
    assert "run_command" not in names
    assert ext.get_external_disabled_tool_names("native") == {"run_command"}
    assert ext.has_external_tool("run_command", include_disabled=True) is True
    assert ext.has_external_tool("run_command", include_disabled=False) is False


@pytest.mark.asyncio
async def test_context_payload_and_reserved_arg_filtering(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_tools_repo(
        repo,
        runner_body=textwrap.dedent(
            """
            async def execute_tool_async(*, tools_dir, tool, args=None, context=None):
                assert context['session_id'] == 'sess-1'
                if tool == 'context_read_ref':
                    assert context['message_id'] == 'm1'
                    assert context['task_id'] == 't1'
                assert context['workspace_dir'].endswith('/workspace')
                assert context['portal_metadata']['legacy_runtime_src_dir']
                assert context['portal_metadata']['context_blob_dir'].endswith('/context_blobs')
                if tool == 'context_read_ref':
                    assert context['portal_metadata']['x'] == 'y'
                    assert context['portal_metadata']['context_blob_dir'] != '/bad'
                assert '_session_id' not in args
                assert '_message_id' not in args
                assert '_task_id' not in args
                assert '_runtime_type' not in args
                assert '_portal_metadata' not in args
                return {'success': True, 'content': f"ok:{tool}:{tools_dir}"}
            """
        ),
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    monkeypatch.setenv("EFP_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("EFP_CONTEXT_BLOB_DIR", str(tmp_path / "context_blobs"))
    _reload_external_loader()

    result = await src_module.execute_tool(
        "context_read_ref",
        ref="ctx://context/sess-1/jira/abcdef123456",
        _session_id="sess-1",
        _message_id="m1",
        _task_id="t1",
        _portal_metadata={"x": "y", "context_blob_dir": "/bad"},
        _runtime_type="native",
    )
    assert result.success is True
    git_result = await src_module.execute_tool("git_status", workspace=".", _session_id="sess-1")
    assert git_result.success is True


@pytest.mark.asyncio
async def test_disabled_descriptor_blocks_legacy_and_exec_alias(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_tools_repo(repo)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    _reload_external_loader()

    result = await src_module.execute_tool("run_command", cmd="echo hi")
    assert result.success is False
    assert "disabled" in (result.error or "").lower()
    alias_result = await src_module.execute_tool("exec", command="ls")
    assert alias_result.success is False
    assert "disabled" in (alias_result.error or "").lower()


def test_validation_errors_non_strict_disable_external(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _create_tools_repo(repo, validate_errors="['bad descriptor']")
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    monkeypatch.setenv("EFP_EXTERNAL_TOOLS_STRICT", "false")
    ext = _reload_external_loader()

    state = ext.load_external_tools_state()
    assert state.available is False
    assert state.validation_errors == ["bad descriptor"]
    assert ext.get_external_disabled_tool_names("native") == {"run_command"}
    assert ext.has_external_tool("run_command", include_disabled=True) is True
    assert ext.has_external_tool("run_command", include_disabled=False) is False
    assert ext.get_external_tool_schemas("native") == []


def test_validation_errors_strict_raise(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _create_tools_repo(repo, validate_errors="['bad descriptor']")
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    monkeypatch.setenv("EFP_EXTERNAL_TOOLS_STRICT", "true")
    ext = _reload_external_loader()

    with pytest.raises(RuntimeError):
        ext.load_external_tools_state()


def test_dict_descriptor_back_compat(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_tools_repo(repo, dataclass_contract=False)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    _reload_external_loader()
    schemas = src_module.get_tools_schema()
    names = {(s.get("function", {}) or {}).get("name") or s.get("name") for s in schemas}
    assert "run_command" in names


@pytest.mark.asyncio
async def test_invalid_registry_still_blocks_disabled_legacy_tools(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_tools_repo(repo, validate_errors="['bad descriptor']")
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    monkeypatch.setenv("EFP_EXTERNAL_TOOLS_STRICT", "false")
    _reload_external_loader()

    names = {(s.get("function", {}) or {}).get("name") or s.get("name") for s in src_module.get_tools_schema()}
    assert "run_command" not in names
    result = await src_module.execute_tool("run_command", cmd="echo hi")
    assert result.success is False
    assert "disabled" in (result.error or "").lower()
    alias_result = await src_module.execute_tool("exec", command="ls")
    assert alias_result.success is False
    assert "disabled" in (alias_result.error or "").lower()


@pytest.mark.asyncio
async def test_execute_external_tool_direct_returns_error_on_runner_exception(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _create_tools_repo(
        repo,
        runner_body="async def execute_tool_async(**kwargs):\n    raise RuntimeError('runner boom')\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    ext = _reload_external_loader()

    result = await ext.execute_external_tool(
        "context_read_ref",
        {"ref": "ctx://context/sess-1/jira/abcdef123456", "_session_id": "sess-1"},
        session_id="sess-1",
        runtime_type="native",
    )
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert "execution failed" in (result.get("error") or "")


def test_strict_mode_schema_load_does_not_fallback(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_tools_repo(repo, validate_errors="['bad descriptor']")
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    monkeypatch.setenv("EFP_EXTERNAL_TOOLS_STRICT", "true")
    _reload_external_loader()
    with pytest.raises(RuntimeError):
        src_module.get_tools_schema()


@pytest.mark.asyncio
async def test_strict_mode_execute_does_not_fallback_to_legacy(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_tools_repo(repo, validate_errors="['bad descriptor']")
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    monkeypatch.setenv("EFP_EXTERNAL_TOOLS_STRICT", "true")
    _reload_external_loader()
    result = await src_module.execute_tool("run_command", cmd="echo hi")
    assert result.success is False
    assert "strict mode failed" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_execute_external_tool_known_enabled_unavailable_returns_structured_error(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _create_tools_repo(repo, validate_errors="['bad descriptor']")
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    monkeypatch.setenv("EFP_EXTERNAL_TOOLS_STRICT", "false")
    ext = _reload_external_loader()
    result = await ext.execute_external_tool(
        "context_read_ref",
        {"ref": "ctx://context/sess-1/jira/abcdef123456", "_session_id": "sess-1"},
        session_id="sess-1",
        runtime_type="native",
    )
    assert result["success"] is False
    assert "External tools unavailable" in result["error"]


@pytest.mark.asyncio
async def test_execute_external_tool_runner_none_returns_structured_error(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _create_tools_repo(repo, runner_body="async def execute_tool_async(**kwargs):\n    return None\n")
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    ext = _reload_external_loader()
    result = await ext.execute_external_tool(
        "context_read_ref",
        {"ref": "ctx://context/sess-1/jira/abcdef123456", "_session_id": "sess-1"},
        session_id="sess-1",
        runtime_type="native",
    )
    assert result["success"] is False
    assert "returned no result" in result["error"]


def test_optional_real_tools_repo_fixture(monkeypatch):
    fixture = os.environ.get("EFP_TOOLS_REPO_FIXTURE")
    if not fixture:
        pytest.skip("EFP_TOOLS_REPO_FIXTURE not set")

    monkeypatch.setenv("EFP_TOOLS_DIR", fixture)
    ext = _reload_external_loader()
    state = ext.load_external_tools_state()
    assert state.available is True
    descriptors = ext._iter_descriptors(state)
    assert len(descriptors) >= 50
    assert len(ext.get_external_tool_schemas("native")) >= 30
    disabled = ext.get_external_disabled_tool_names("native")
    assert "run_command" in disabled
    assert "write" in disabled
    schema_names = {(s.get("function", {}) or {}).get("name") or s.get("name") for s in ext.get_external_tool_schemas("native")}
    assert "context_read_ref" in schema_names
    assert "git_status" in schema_names
    assert ext.has_external_tool("write", include_disabled=True) is True
    assert ext.has_external_tool("write", include_disabled=False) is False


def test_strip_none_values_behavior(src_module):
    payload = src_module._strip_none_values({"a": None, "b": False, "c": 0, "d": ""})
    assert "a" not in payload
    assert payload["b"] is False
    assert payload["c"] == 0
    assert payload["d"] == ""
