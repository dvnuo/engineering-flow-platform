from aiohttp import web


def _write_tools_repo(tmp_path, *, name="context_echo", enabled=True, metadata=None):
    metadata = metadata or {}
    tools_dir = tmp_path / "tools_repo"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "manifest.yaml").write_text(
        f"""
tool_id: efp.tool.context.echo
name: {name}
description: Echo
python_entrypoint: test_tools.echo:execute
input_schema:
  type: object
  properties:
    text:
      type: string
output_schema:
  type: object
runtime_compat: [native, opencode]
policy_tags: [context, read_only]
domain: context
opencode_name: efp_context_echo
mutation: false
risk_level: low
metadata: {metadata}
enabled: {str(enabled).lower()}
""",
        encoding="utf-8",
    )
    (tools_dir / "python" / "test_tools").mkdir(parents=True)
    (tools_dir / "python" / "test_tools" / "__init__.py").write_text("", encoding="utf-8")
    (tools_dir / "python" / "test_tools" / "echo.py").write_text("def execute(**kwargs):\n    return {'success': True, 'content': 'ok'}\n", encoding="utf-8")
    return tools_dir


def test_capability_snapshot_contract_shape():
    from src.runtime.capability_registry import build_default_capability_registry
    snapshot = build_default_capability_registry().export_catalog_snapshot()
    assert {"capabilities", "count", "catalog_version", "generated_at"}.issubset(snapshot.keys())
    assert isinstance(snapshot["capabilities"], list)
    assert snapshot["count"] == len(snapshot["capabilities"])
    assert snapshot["generated_at"].endswith("Z")
    types = {c.get("type") for c in snapshot["capabilities"]}
    assert {"tool", "skill", "adapter_action"}.issubset(types)


def test_external_tool_appears_in_capability_snapshot(monkeypatch, tmp_path):
    from src.runtime.capability_registry import build_default_capability_registry
    from src.tools_external import reset_external_tool_registry_cache
    tools_dir = _write_tools_repo(tmp_path, metadata={"source": "legacy_efp"})
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()
    snapshot = build_default_capability_registry().export_catalog_snapshot()
    cap = next(c for c in snapshot["capabilities"] if c.get("type") == "tool" and c.get("name") == "context_echo")
    assert cap["capability_id"] == "efp.tool.context.echo"
    assert cap["metadata"]["external_tool"] is True
    assert cap["metadata"]["tool_id"] == "efp.tool.context.echo"
    assert cap["metadata"]["domain"] == "context"
    assert "read_only" in cap["metadata"]["policy_tags"]
    assert "native" in cap["metadata"]["runtime_compat"]


def test_disabled_external_tool_absent_from_capability_snapshot(monkeypatch, tmp_path):
    from src.runtime.capability_registry import build_default_capability_registry
    from src.tools_external import reset_external_tool_registry_cache
    tools_dir = _write_tools_repo(tmp_path, name="external_disabled_write", enabled=False)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()
    snapshot = build_default_capability_registry().export_catalog_snapshot()
    assert not any(c.get("name") == "external_disabled_write" for c in snapshot["capabilities"])


def test_duplicate_disabled_external_tool_does_not_override_legacy_capability(monkeypatch, tmp_path):
    from src.runtime.capability_registry import build_default_capability_registry
    from src.tools_external import reset_external_tool_registry_cache
    tools_dir = _write_tools_repo(tmp_path, name="run_command", enabled=False)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_dir))
    reset_external_tool_registry_cache()
    cap = next(c for c in build_default_capability_registry().list_by_type("tool") if c.name == "run_command")
    assert cap.metadata.get("external_tool") is not True
    assert cap.source_ref == "src.__init__.get_tools_schema"


def test_runtime_gateway_routes_include_t13_native_contract():
    from src.gateway.server import Gateway
    server = Gateway()
    app = server.app
    got = {(r.method, r.resource.canonical) for r in app.router.routes()}
    required = {
        ("GET", "/health"), ("GET", "/actuator/health"), ("GET", "/api/queue/status"),
        ("GET", "/api/events"),
        ("POST", "/api/chat"), ("POST", "/api/chat/stream"), ("POST", "/api/tasks/execute"),
        ("POST", "/api/tasks/{task_id}/cancel"),
        ("GET", "/api/tasks/{task_id}"), ("GET", "/api/capabilities"), ("POST", "/api/internal/runtime-profile/apply"),
        ("GET", "/api/skills"), ("GET", "/api/usage"), ("GET", "/api/sessions"),
    }
    assert required.issubset(got)
