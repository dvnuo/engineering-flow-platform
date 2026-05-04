from aiohttp import web


def test_capability_snapshot_contract_shape():
    from src.runtime.capability_registry import build_default_capability_registry
    snapshot = build_default_capability_registry().export_catalog_snapshot()
    assert {"capabilities", "count", "catalog_version", "generated_at"}.issubset(snapshot.keys())
    assert isinstance(snapshot["capabilities"], list)
    assert snapshot["count"] == len(snapshot["capabilities"])
    assert snapshot["generated_at"].endswith("Z")
    types = {c.get("type") for c in snapshot["capabilities"]}
    assert {"tool", "skill", "adapter_action"}.issubset(types)


def test_runtime_gateway_routes_include_t13_native_contract():
    from src.gateway.server import Gateway
    server = Gateway()
    app = server.app
    got = {(r.method, r.resource.canonical) for r in app.router.routes()}
    required = {
        ("GET", "/health"), ("GET", "/actuator/health"), ("GET", "/api/queue/status"),
        ("POST", "/api/chat"), ("POST", "/api/chat/stream"), ("POST", "/api/tasks/execute"),
        ("GET", "/api/tasks/{task_id}"), ("GET", "/api/capabilities"), ("POST", "/api/internal/runtime-profile/apply"),
        ("GET", "/api/skills"), ("GET", "/api/usage"), ("GET", "/api/sessions"),
    }
    assert required.issubset(got)
