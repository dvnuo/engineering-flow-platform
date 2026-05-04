import json

import pytest


@pytest.fixture(autouse=True)
def _reset_external_registry_cache():
    from src.tools_external import reset_external_tool_registry_cache

    reset_external_tool_registry_cache()
    yield
    reset_external_tool_registry_cache()


def _write_tools_repo(
    tmp_path,
    *,
    name="context_echo",
    description="Echo input for tests.",
    runtime_compat=None,
    metadata=None,
    domain="context",
    manifest_subpath="manifest.yaml",
    entrypoint_body=None,
    extra_fields=None,
):
    runtime_compat = runtime_compat or ["native", "opencode"]
    metadata = metadata or {}
    extra_fields = extra_fields or {}
    extra_yaml = "".join(f"{key}: {json.dumps(value)}\n" for key, value in extra_fields.items())

    tools_dir = tmp_path / "tools_repo"
    (tools_dir / "python" / "test_tools").mkdir(parents=True)
    (tools_dir / "python" / "test_tools" / "__init__.py").write_text("", encoding="utf-8")
    (tools_dir / "python" / "test_tools" / "echo.py").write_text(
        entrypoint_body or "async def execute(text='', **kwargs):\n    return {'echo': text, 'kwargs': kwargs}\n",
        encoding="utf-8",
    )

    manifest_path = tools_dir / manifest_subpath
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        """
tool_id: efp.tool.context.echo
name: {name}
description: {description}
python_entrypoint: test_tools.echo:execute
input_schema:
  type: object
  properties:
    text:
      type: string
  required: [text]
output_schema:
  type: object
runtime_compat: {runtime_compat}
policy_tags: [read_only]
domain: {domain}
metadata: {metadata}
{extra_yaml}
""".format(
            name=name,
            description=description,
            runtime_compat=json.dumps(runtime_compat),
            domain=domain,
            metadata=json.dumps(metadata),
            extra_yaml=extra_yaml,
        ),
        encoding="utf-8",
    )
    return tools_dir


def _schema_name(schema):
    return (schema.get("function") or {}).get("name")


def test_missing_external_tools_dir_does_not_affect_legacy(monkeypatch, tmp_path):
    from src import get_tool_names
    from src.tools_external import get_external_tool_registry

    monkeypatch.setenv("EFP_TOOLS_DIR", str(tmp_path / "missing"))
    registry = get_external_tool_registry(force_reload=True)
    assert registry.get_tool_names() == []

    names = set(get_tool_names())
    assert "run_command" in names
    assert "git_clone" in names
    assert "jira_get_issue" in names
    assert "context_read_ref" in names


def test_manifest_loads(monkeypatch, tmp_path):
    from src.tools_external import get_external_tool_registry

    tools_dir = _write_tools_repo(tmp_path)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    registry = get_external_tool_registry(force_reload=True)
    descriptors = registry.list_descriptors()
    assert len(descriptors) == 1
    descriptor = descriptors[0]
    assert descriptor.name == "context_echo"
    assert descriptor.python_entrypoint == "test_tools.echo:execute"
    assert "native" in descriptor.runtime_compat


@pytest.mark.asyncio
async def test_fixture_python_entrypoint_executes(monkeypatch, tmp_path):
    from src.tools_external import get_external_tool_registry

    tools_dir = _write_tools_repo(tmp_path)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    registry = get_external_tool_registry(force_reload=True)

    result = await registry.execute_tool("context_echo", text="hello")
    assert result.success is True
    assert "hello" in result.content


def test_get_tools_schema_contains_external(monkeypatch, tmp_path):
    from src import get_tools_schema
    from src.tools_external import reset_external_tool_registry_cache

    tools_dir = _write_tools_repo(tmp_path)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()
    schemas = get_tools_schema()
    names = {_schema_name(item) for item in schemas if isinstance(item, dict)}
    assert "context_echo" in names
    schema = next(item for item in schemas if _schema_name(item) == "context_echo")
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "context_echo"
    assert schema["function"]["parameters"]["required"] == ["text"]


@pytest.mark.asyncio
async def test_src_execute_tool_calls_external_append_only(monkeypatch, tmp_path):
    from src import execute_tool
    from src.tools_external import reset_external_tool_registry_cache

    tools_dir = _write_tools_repo(tmp_path)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()
    result = await execute_tool("context_echo", text="hello")
    assert result.success is True
    assert "hello" in result.content


def test_duplicate_legacy_name_without_override_does_not_replace_schema(monkeypatch, tmp_path):
    from src import get_tools_schema
    from src.tools_external import reset_external_tool_registry_cache

    description = "External run command should not appear"
    tools_dir = _write_tools_repo(tmp_path, name="run_command", description=description)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()
    schemas = get_tools_schema()
    run_command_schemas = [item for item in schemas if _schema_name(item) == "run_command"]
    assert len(run_command_schemas) == 1
    assert run_command_schemas[0]["function"]["description"] != description


@pytest.mark.asyncio
async def test_allow_override_replaces_schema_and_execution(monkeypatch, tmp_path):
    from src import execute_tool, get_tools_schema
    from src.tools_external import reset_external_tool_registry_cache

    description = "External run command override"
    tools_dir = _write_tools_repo(
        tmp_path,
        name="run_command",
        description=description,
        metadata={"allow_override": True},
        entrypoint_body="async def execute(cmd='', **kwargs):\n    return {'override': True, 'cmd': cmd}\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()

    schemas = get_tools_schema()
    schema = next(item for item in schemas if _schema_name(item) == "run_command")
    assert schema["function"]["description"] == description

    result = await execute_tool("run_command", cmd="not-a-real-command")
    assert result.success is True
    data = json.loads(result.content)
    assert data["override"] is True


def test_runtime_compat_without_native_is_skipped(monkeypatch, tmp_path):
    from src import get_tools_schema
    from src.tools_external import get_external_tool_registry

    tools_dir = _write_tools_repo(tmp_path, runtime_compat=["opencode"])
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    from src.tools_external import reset_external_tool_registry_cache
    reset_external_tool_registry_cache()

    registry = get_external_tool_registry(force_reload=True)
    assert "context_echo" not in registry.get_tool_names()
    names = {_schema_name(item) for item in get_tools_schema() if isinstance(item, dict)}
    assert "context_echo" not in names


def test_tools_subdir_yaml_is_loaded(monkeypatch, tmp_path):
    from src.tools_external import get_external_tool_registry

    tools_dir = _write_tools_repo(tmp_path, manifest_subpath="tools/custom/context_echo.yaml")
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    from src.tools_external import reset_external_tool_registry_cache
    reset_external_tool_registry_cache()
    registry = get_external_tool_registry(force_reload=True)
    assert "context_echo" in registry.get_tool_names()


def test_capability_registry_includes_external_metadata(monkeypatch, tmp_path):
    from src.runtime.capability_registry import build_default_capability_registry
    from src.tools_external import reset_external_tool_registry_cache

    tools_dir = _write_tools_repo(tmp_path)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()
    registry = build_default_capability_registry()
    tool_caps = registry.list_by_type("tool")
    cap = next(item for item in tool_caps if item.name == "context_echo")
    assert "read_only" in cap.policy_tags
    assert cap.metadata["external_tool"] is True
    assert cap.metadata["domain"] == "context"
    assert "native" in cap.metadata["runtime_compat"]
    assert cap.input_schema["required"] == ["text"]


def test_duplicate_without_override_not_marked_external(monkeypatch, tmp_path):
    from src.runtime.capability_registry import build_default_capability_registry
    from src.tools_external import reset_external_tool_registry_cache

    description = "External run command should not appear"
    tools_dir = _write_tools_repo(tmp_path, name="run_command", description=description)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()

    registry = build_default_capability_registry()
    cap = next(item for item in registry.list_by_type("tool") if item.name == "run_command")
    assert cap.metadata.get("external_tool") is not True
    assert cap.metadata.get("description") != description


def test_allow_override_marks_capability_external(monkeypatch, tmp_path):
    from src.runtime.capability_registry import build_default_capability_registry
    from src.tools_external import reset_external_tool_registry_cache

    tools_dir = _write_tools_repo(tmp_path, name="run_command", metadata={"allow_override": True})
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()

    registry = build_default_capability_registry()
    cap = next(item for item in registry.list_by_type("tool") if item.name == "run_command")
    assert cap.metadata["external_tool"] is True
    assert cap.metadata["allow_override"] is True


def test_top_level_requires_identity_binding_is_preserved_in_descriptor_and_capability(monkeypatch, tmp_path):
    from src.runtime.capability_registry import build_default_capability_registry
    from src.tools_external import get_external_tool_registry, reset_external_tool_registry_cache

    tools_dir = _write_tools_repo(
        tmp_path,
        name="github_external_lookup",
        domain="github",
        extra_fields={
            "requires_identity_binding": True,
            "opencode_name": "efp_github_external_lookup",
        },
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()

    external_registry = get_external_tool_registry(force_reload=True)
    descriptor = external_registry.get_descriptor("github_external_lookup")
    assert descriptor is not None
    assert descriptor.requires_identity_binding is True
    assert descriptor.metadata["requires_identity_binding"] is True
    assert descriptor.metadata["opencode_name"] == "efp_github_external_lookup"

    registry = build_default_capability_registry()
    cap = next(item for item in registry.list_by_type("tool") if item.name == "github_external_lookup")
    assert cap.requires_identity_binding is True
    assert cap.metadata["requires_identity_binding"] is True
    assert cap.metadata["opencode_name"] == "efp_github_external_lookup"


def test_external_capability_id_preserves_tool_id(monkeypatch, tmp_path):
    from src.runtime.capability_registry import build_default_capability_registry
    from src.tools_external import reset_external_tool_registry_cache

    tools_dir = _write_tools_repo(tmp_path)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()

    registry = build_default_capability_registry()
    cap = next(item for item in registry.list_by_type("tool") if item.name == "context_echo")
    assert cap.capability_id == "efp.tool.context.echo"
    assert registry.exists("efp.tool.context.echo") is True


@pytest.mark.asyncio
async def test_tool_result_like_dict_preserves_failure(monkeypatch, tmp_path):
    from src import execute_tool
    from src.tools_external import reset_external_tool_registry_cache

    tools_dir = _write_tools_repo(
        tmp_path,
        name="context_failure",
        entrypoint_body="def execute(**kwargs):\n    return {'success': False, 'content': '', 'error': 'boom'}\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()

    result = await execute_tool("context_failure")
    assert result.success is False
    assert result.error == "boom"

def test_t04_root_manifest_is_not_treated_as_descriptor(monkeypatch, tmp_path):
    from src.tools_external import get_external_tool_registry

    tools_dir = _write_tools_repo(tmp_path, manifest_subpath="tools/context/context_echo.yaml")
    (tools_dir / "manifest.yaml").write_text('repo: engineering-flow-platform-tools\nversion: 0.1.0\nschema_version: t04\n', encoding="utf-8")
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    registry = get_external_tool_registry(force_reload=True)
    assert [d.name for d in registry.list_descriptors()] == ["context_echo"]


@pytest.mark.asyncio
async def test_disabled_external_only_tool_is_hidden_and_blocked(monkeypatch, tmp_path):
    from src import execute_tool, get_tools_schema
    from src.runtime.capability_registry import build_default_capability_registry
    from src.tools_external import reset_external_tool_registry_cache

    tools_dir = _write_tools_repo(tmp_path, name="external_disabled_write", extra_fields={"enabled": False})
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()
    assert "external_disabled_write" not in {_schema_name(s) for s in get_tools_schema() if isinstance(s, dict)}
    result = await execute_tool("external_disabled_write")
    assert result.success is False and "disabled" in (result.error or "")
    cap_names = {c.name for c in build_default_capability_registry().list_by_type("tool")}
    assert "external_disabled_write" not in cap_names


def test_real_t04_like_disabled_exec_not_exposed(monkeypatch, tmp_path):
    from src import get_tools_schema
    from src.tools_external import get_external_tool_registry
    tools_dir = _write_tools_repo(tmp_path, name="exec", extra_fields={"enabled": False, "policy_tags": ["bash", "mutation", "disabled_until_safety_review"]})
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    registry = get_external_tool_registry(force_reload=True)
    assert "exec" not in registry.get_tool_names()
    assert "exec" not in {_schema_name(s) for s in get_tools_schema() if isinstance(s, dict)}


def test_model_facing_false_is_hidden_from_llm_schema(monkeypatch, tmp_path):
    from src import get_tools_schema
    from src.tools_external import get_external_tool_registry

    tools_dir = _write_tools_repo(tmp_path, name="context_hidden", metadata={"model_facing": False})
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    registry = get_external_tool_registry(force_reload=True)
    assert registry.get_descriptor("context_hidden") is not None
    assert "context_hidden" not in registry.get_tool_names()
    assert "context_hidden" not in {_schema_name(s) for s in get_tools_schema() if isinstance(s, dict)}


@pytest.mark.asyncio
async def test_model_facing_false_allow_override_does_not_override_legacy_schema_or_execution(monkeypatch, tmp_path):
    from src import execute_tool, get_tools_schema
    from src.tools_external import get_external_tool_registry, reset_external_tool_registry_cache
    import src.bash_tools as bash_tools_module

    tools_dir = _write_tools_repo(
        tmp_path,
        name="run_command",
        description="external run_command hidden",
        metadata={"allow_override": True, "model_facing": False},
        entrypoint_body="def execute(**kwargs):\n    return {'success': True, 'content': 'external override ran'}\n",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()

    schemas = [s for s in get_tools_schema() if _schema_name(s) == "run_command"]
    assert len(schemas) == 1
    assert schemas[0]["function"]["description"] != "external run_command hidden"

    async def _fake_run_command(cmd, args=None, cwd=None, timeout_ms=15000):
        return {"ok": True, "exit_code": 0, "stdout": "legacy run_command ran", "stderr": "", "truncated": {}}

    monkeypatch.setattr(bash_tools_module, "run_command", _fake_run_command)
    result = await execute_tool("run_command", cmd="anything")
    assert "external override ran" not in result.content
    assert "legacy run_command ran" in result.content

    registry = get_external_tool_registry(force_reload=True)
    assert registry.is_override_enabled("run_command") is False


@pytest.mark.asyncio
async def test_t04_runner_receives_context_and_reserved_args_are_filtered(monkeypatch, tmp_path):
    from src.tools_external import get_external_tool_registry, reset_external_tool_registry_cache

    tools_dir = tmp_path / "tools_repo"
    (tools_dir / "tools" / "context").mkdir(parents=True)
    (tools_dir / "python" / "efp_tools").mkdir(parents=True)
    (tools_dir / "python" / "efp_tools" / "__init__.py").write_text("", encoding="utf-8")
    (tools_dir / "manifest.yaml").write_text('repo: engineering-flow-platform-tools\nversion: "0.1.0"\nschema_version: t04\n', encoding="utf-8")
    (tools_dir / "tools" / "context" / "context_echo.yaml").write_text(
        """
name: context_echo
tool_id: efp.tool.context.echo
opencode_name: efp_context_echo
description: Echo via T04 runner
domain: context
type: adapter_action
runtime_compat: [native, opencode]
policy_tags: [context, read_only]
requires_identity_binding: false
mutation: false
risk_level: low
python_entrypoint: ignored.module:ignored
input_schema:
  type: object
  properties:
    text: {type: string}
  required: [text]
output_schema:
  type: object
metadata:
  source: fixture
enabled: true
""",
        encoding="utf-8",
    )
    (tools_dir / "python" / "efp_tools" / "runner.py").write_text(
        """
async def execute_tool_async(*, tools_dir, tool, args=None, context=None):
    assert tool == 'context_echo'
    assert args == {'text': 'hello'}
    assert context['runtime_type'] == 'native'
    assert context['session_id'] == 'sess-1'
    assert context['message_id'] == 'msg-1'
    assert context['task_id'] == 'task-1'
    assert context['workspace_dir'].endswith('workspace-override')
    assert context['portal_metadata']['x'] == 'y'
    assert context['portal_metadata']['legacy_runtime_src_dir']
    assert context['portal_metadata']['context_blob_dir'].endswith('context_blobs')
    assert context['opencode_context'] == {'ignored': 'but preserved'}
    return {'success': True, 'content': 'hello'}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()
    registry = get_external_tool_registry(force_reload=True)
    result = await registry.execute_tool(
        "context_echo",
        text="hello",
        _session_id="sess-1",
        _message_id="msg-1",
        _task_id="task-1",
        _runtime_type="native",
        _workspace_dir=str(tmp_path / "workspace-override"),
        _portal_metadata={"x": "y"},
        _opencode_context={"ignored": "but preserved"},
    )
    assert result.success is True
    assert result.content == "hello"


def test_real_t04_enabled_tool_schema_has_metadata(monkeypatch, tmp_path):
    from src import get_tools_schema
    from src.tools_external import reset_external_tool_registry_cache

    tools_dir = tmp_path / "tools_repo_meta"
    (tools_dir / "python" / "test_tools").mkdir(parents=True)
    (tools_dir / "python" / "test_tools" / "__init__.py").write_text("", encoding="utf-8")
    (tools_dir / "python" / "test_tools" / "echo.py").write_text("def execute(**kwargs):\n    return {'success': True, 'content': 'ok'}\n", encoding="utf-8")
    (tools_dir / "manifest.yaml").write_text(
        json.dumps({
            "tool_id": "efp.tool.context.echo",
            "name": "context_echo",
            "description": "Echo",
            "python_entrypoint": "test_tools.echo:execute",
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            "output_schema": {"type": "object"},
            "runtime_compat": ["native", "opencode"],
            "policy_tags": ["context", "read_only"],
            "domain": "context",
            "opencode_name": "efp_context_echo",
            "mutation": False,
            "risk_level": "low",
            "metadata": {"source": "legacy_efp"},
            "enabled": True,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()
    schema = next(s for s in get_tools_schema() if _schema_name(s) == "context_echo")
    md = schema["metadata"]
    assert md["source"] == "external_tools_repo"
    assert md["descriptor_source"] == "legacy_efp"
    assert md["tool_id"] == "efp.tool.context.echo"
    assert md["domain"] == "context"
    assert md["policy_tags"] == ["context", "read_only"]
    assert md["requires_identity_binding"] is False
    assert md["mutation"] is False
    assert md["risk_level"] == "low"
    assert "native" in md["runtime_compat"]
    assert md["opencode_name"] == "efp_context_echo"
    assert md["enabled"] is True


def test_external_capability_metadata_preserves_contract_fields(monkeypatch, tmp_path):
    from src.runtime.capability_registry import build_default_capability_registry
    from src.tools_external import reset_external_tool_registry_cache

    tools_dir = tmp_path / "tools_repo_capmeta"
    (tools_dir / "python" / "test_tools").mkdir(parents=True)
    (tools_dir / "python" / "test_tools" / "__init__.py").write_text("", encoding="utf-8")
    (tools_dir / "python" / "test_tools" / "echo.py").write_text("def execute(**kwargs):\n    return {'success': True, 'content': 'ok'}\n", encoding="utf-8")
    (tools_dir / "manifest.yaml").write_text(
        json.dumps({
            "tool_id": "efp.tool.context.echo",
            "name": "context_echo",
            "description": "Echo",
            "python_entrypoint": "test_tools.echo:execute",
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            "output_schema": {"type": "object"},
            "runtime_compat": ["native", "opencode"],
            "policy_tags": ["context", "read_only"],
            "domain": "context",
            "opencode_name": "efp_context_echo",
            "mutation": False,
            "risk_level": "low",
            "metadata": {"source": "legacy_efp"},
            "enabled": True,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()
    cap = next(c for c in build_default_capability_registry().list_by_type("tool") if c.name == "context_echo")
    assert cap.capability_id == "efp.tool.context.echo"
    assert cap.metadata["external_tool"] is True
    assert cap.metadata["tool_id"] == "efp.tool.context.echo"
    assert cap.metadata["policy_tags"] == ["context", "read_only"]
    assert cap.metadata["requires_identity_binding"] is False
    assert cap.metadata["domain"] == "context"
    assert "native" in cap.metadata["runtime_compat"]
    assert cap.metadata["opencode_name"] == "efp_context_echo"
    assert cap.metadata["mutation"] is False
    assert cap.metadata["risk_level"] == "low"
