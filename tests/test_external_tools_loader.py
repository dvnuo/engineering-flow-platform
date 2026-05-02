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


def _create_tools_repo(base_dir, *, descriptors_expr: str, runner_body: str, dataclass_contract: bool = False, validation_errors_expr: str = "[]"):
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
            "from efp_tools.contracts import ToolDescriptor\n\n"
            f"DESCRIPTORS = {descriptors_expr}\n\n"
            "class Registry:\n"
            "    def list_all_descriptors(self):\n"
            "        return list(DESCRIPTORS)\n\n"
            "    def list_descriptors(self, *, runtime_type=None, enabled_only=True, model_facing_only=True):\n"
            "        items = list(DESCRIPTORS)\n"
            "        if runtime_type:\n"
            "            items = [d for d in items if runtime_type in (d.runtime_compat or [])]\n"
            "        if enabled_only:\n"
            "            items = [d for d in items if d.enabled]\n"
            "        if model_facing_only:\n"
            "            items = [d for d in items if (d.metadata or {}).get('model_facing', True) is not False]\n"
            "        return items\n\n"
            f"    def validate(self):\n        return {validation_errors_expr}\n\n"
            "def load_registry(tools_dir):\n"
            "    return Registry()\n",
            encoding="utf-8",
        )
    else:
        (pkg_dir / "contracts.py").write_text("", encoding="utf-8")
        (pkg_dir / "registry.py").write_text(
            f"""DESCRIPTORS = {descriptors_expr}

class Registry:
    def list_descriptors(self, **kwargs):
        return DESCRIPTORS

def load_registry(tools_dir):
    return Registry()
""",
            encoding="utf-8",
        )
    (pkg_dir / "runner.py").write_text(runner_body, encoding="utf-8")


def test_missing_tools_dir_falls_back_to_legacy(monkeypatch, tmp_path, src_module):
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tmp_path / "does-not-exist"))
    ext = _reload_external_loader()
    assert src_module.get_tools_schema()
    assert ext.load_external_tools_state().available is False


def test_dataclass_descriptors_exposed_with_input_schema_and_metadata(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _create_tools_repo(
        repo,
        dataclass_contract=True,
        descriptors_expr="""[
            ToolDescriptor(
                tool_id='efp.tool.context.context_read_ref',
                name='context_read_ref',
                opencode_name='context_read_ref',
                description='Read context ref',
                domain='context',
                type='read',
                runtime_compat=['native'],
                policy_tags=['read'],
                requires_identity_binding=False,
                mutation=False,
                risk_level='low',
                python_entrypoint='efp_tools.impl:context_read_ref',
                input_schema={'type':'object','properties':{'ref':{'type':'string'}},'required':['ref']},
                output_schema={'type':'object'},
                metadata={'model_facing': True},
                enabled=True,
            ),
            ToolDescriptor(
                tool_id='efp.tool.git.git_status',
                name='git_status',
                opencode_name='git_status',
                description='Git status',
                domain='git',
                type='read',
                runtime_compat=['native'],
                policy_tags=['read'],
                requires_identity_binding=False,
                mutation=False,
                risk_level='low',
                python_entrypoint='efp_tools.impl:git_status',
                input_schema={'type':'object','properties':{'workspace':{'type':'string'}}},
                output_schema={'type':'object'},
                metadata={'model_facing': True},
                enabled=True,
            ),
            ToolDescriptor(
                tool_id='efp.tool.bash.run_command',
                name='run_command',
                opencode_name='run_command',
                description='Run command',
                domain='bash',
                type='write',
                runtime_compat=['native'],
                policy_tags=['exec'],
                requires_identity_binding=False,
                mutation=True,
                risk_level='high',
                python_entrypoint='efp_tools.impl:run_command',
                input_schema={'type':'object','properties':{'cmd':{'type':'string'}}},
                output_schema={'type':'object'},
                metadata={'model_facing': True},
                enabled=False,
            ),
        ]""",
        runner_body="async def execute_tool_async(**kwargs):\n    return {'success': True, 'content': 'ok'}\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    ext = _reload_external_loader()

    schemas = ext.get_external_tool_schemas(runtime_type="native")
    assert schemas
    schema = schemas[0]
    assert schema["function"]["name"] == "context_read_ref"
    assert schema["function"]["parameters"]["properties"]["ref"]["type"] == "string"
    assert schema["metadata"]["source"] == "external_tools_repo"
    assert schema["metadata"]["tool_id"] == "efp.tool.context.context_read_ref"
    assert ext.get_external_disabled_tool_names("native") == {"run_command"}
    assert ext.has_external_tool("run_command", include_disabled=True)
    assert not ext.has_external_tool("run_command", include_disabled=False)


@pytest.mark.asyncio
async def test_disabled_descriptors_block_legacy_and_exec_alias(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_tools_repo(
        repo,
        dataclass_contract=True,
        descriptors_expr="""[
            ToolDescriptor('efp.tool.bash.run_command','run_command','run_command','x','bash','write',['native'],['exec'],False,True,'high','x',{'type':'object'},{'type':'object'}, {}, False)
        ]""",
        runner_body="async def execute_tool_async(**kwargs):\n    return {'success': True, 'content': 'unexpected'}\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    ext = _reload_external_loader()

    assert ext.get_external_disabled_tool_names("native") == {"run_command"}
    result = await src_module.execute_tool("run_command", cmd="echo hi")
    assert result.success is False
    assert "disabled" in (result.error or "").lower()

    alias_result = await src_module.execute_tool("exec", command="ls")
    assert alias_result.success is False
    assert "disabled" in (alias_result.error or "").lower()


@pytest.mark.asyncio
async def test_runner_uses_t04_async_signature_and_context(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_tools_repo(
        repo,
        dataclass_contract=True,
        descriptors_expr="""[
            ToolDescriptor('efp.tool.context.context_read_ref','context_read_ref','context_read_ref','x','context','read',['native'],['read'],False,False,'low','x',{'type':'object','properties':{'ref':{'type':'string'}}},{'type':'object'}, {}, True)
        ]""",
        runner_body=textwrap.dedent(
            """
            async def execute_tool_async(*, tools_dir, tool, args=None, context=None):
                assert tool == 'context_read_ref'
                assert args['ref'] == 'ctx://context/sess-1/jira/abcdef123456'
                assert '_session_id' not in (args or {})
                assert context['runtime_type'] == 'native'
                assert context['session_id'] == 'sess-1'
                assert context['workspace_dir']
                assert context['portal_metadata']['legacy_runtime_src_dir']
                assert context['portal_metadata']['context_blob_dir']
                return {'success': True, 'content': f"external ok:{tools_dir}"}
            """
        ),
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    monkeypatch.setenv("EFP_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("EFP_CONTEXT_BLOB_DIR", str(tmp_path / "context_blobs"))
    _reload_external_loader()

    result = await src_module.execute_tool("context_read_ref", ref="ctx://context/sess-1/jira/abcdef123456", _session_id="sess-1")
    assert result.success is True
    assert "external ok:" in result.content


@pytest.mark.asyncio
async def test_reserved_args_filtered_for_runner(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_tools_repo(
        repo,
        dataclass_contract=True,
        descriptors_expr="""[
            ToolDescriptor('efp.tool.git.git_status','git_status','git_status','x','git','read',['native'],['read'],False,False,'low','x',{'type':'object','properties':{'workspace':{'type':'string'}}},{'type':'object'}, {}, True)
        ]""",
        runner_body=textwrap.dedent(
            """
            async def execute_tool_async(*, tools_dir, tool, args=None, context=None):
                assert '_session_id' not in (args or {})
                assert context['session_id'] == 'sess-1'
                return {'success': True, 'content': 'ok'}
            """
        ),
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    _reload_external_loader()
    result = await src_module.execute_tool("git_status", workspace=".", _session_id="sess-1")
    assert result.success is True


@pytest.mark.asyncio
async def test_runner_error_does_not_silent_fallback(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_tools_repo(
        repo,
        dataclass_contract=True,
        descriptors_expr="""[
            ToolDescriptor('efp.tool.context.context_read_ref','context_read_ref','context_read_ref','x','context','read',['native'],['read'],False,False,'low','x',{'type':'object','properties':{'ref':{'type':'string'}}},{'type':'object'}, {}, True)
        ]""",
        runner_body="async def execute_tool_async(**kwargs):\n    return {'success': False, 'error': 'external failed'}\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    _reload_external_loader()

    result = await src_module.execute_tool("context_read_ref", ref="ctx://x")
    assert result.success is False
    assert "external failed" in (result.error or "")


def test_dict_descriptor_back_compat(monkeypatch, tmp_path, src_module):
    repo = tmp_path / "repo"
    _create_tools_repo(
        repo,
        descriptors_expr='[{"name": "run_command", "enabled": True, "runtime_compat": ["native"], "metadata": {}, "schema": {"type":"function", "function": {"name":"run_command", "description":"external run command", "parameters":{"type":"object","properties":{"cmd":{"type":"string"}}}}}}]',
        runner_body="async def execute_tool(name, kwargs, context=None):\n    return {'success': True, 'content': 'ok'}\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    _reload_external_loader()
    schemas = src_module.get_tools_schema()
    by_name = {(s.get("function", {}) or {}).get("name") or s.get("name"): s for s in schemas}
    assert "run_command" in by_name


def test_strip_none_values_behavior(src_module):
    payload = src_module._strip_none_values({"a": None, "b": False, "c": 0, "d": ""})
    assert "a" not in payload
    assert payload["b"] is False
    assert payload["c"] == 0
    assert payload["d"] == ""


def test_validation_errors_non_strict(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _create_tools_repo(
        repo,
        dataclass_contract=True,
        validation_errors_expr="['bad descriptor']",
        descriptors_expr="[]",
        runner_body="async def execute_tool_async(**kwargs):\n    return {'success': True, 'content': 'ok'}\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    monkeypatch.setenv("EFP_EXTERNAL_TOOLS_STRICT", "false")
    ext = _reload_external_loader()
    state = ext.load_external_tools_state()
    assert state.available is False
    assert "bad descriptor" in str(state.validation_errors)
    assert ext.get_external_tool_schemas("native") == []


def test_validation_errors_strict(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    _create_tools_repo(
        repo,
        dataclass_contract=True,
        validation_errors_expr="['bad descriptor']",
        descriptors_expr="[]",
        runner_body="async def execute_tool_async(**kwargs):\n    return {'success': True, 'content': 'ok'}\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(repo))
    monkeypatch.setenv("EFP_EXTERNAL_TOOLS_STRICT", "true")
    ext = _reload_external_loader()
    with pytest.raises(RuntimeError):
        ext.load_external_tools_state()


def test_optional_real_tools_repo_smoke(monkeypatch):
    fixture = os.environ.get("EFP_TOOLS_REPO_FIXTURE")
    if not fixture:
        pytest.skip("EFP_TOOLS_REPO_FIXTURE not set")
    monkeypatch.setenv("EFP_TOOLS_DIR", fixture)
    ext = _reload_external_loader()
    state = ext.load_external_tools_state()
    assert state.available is True
    assert len(ext._iter_descriptors(state)) >= 50
    disabled = ext.get_external_disabled_tool_names("native")
    assert ("write" in disabled) or ("run_command" in disabled)
    names = {s.get("function", {}).get("name") for s in ext.get_external_tool_schemas("native")}
    assert "context_read_ref" in names
    assert "git_status" in names
