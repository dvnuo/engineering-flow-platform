from pathlib import Path


def test_runtime_gateway_frontend_files_are_removed():
    gateway_dir = Path("src") / "gateway"
    assert not (gateway_dir / "static").exists()
    assert not (gateway_dir / "templates").exists()
    removed_module = gateway_dir / ("web" + "chat.py")
    assert not removed_module.exists()


def test_runtime_gateway_does_not_register_embedded_frontend_routes():
    from src.gateway.server import Gateway

    app = Gateway().app
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}

    assert ("GET", "/") not in routes
    static_prefix = "/" + "static/"
    assert not any(method == "GET" and path == static_prefix + "{path}" for method, path in routes)
    assert not any(method == "GET" and path.startswith(static_prefix) for method, path in routes)


def test_runtime_gateway_does_not_register_removed_runtime_routes():
    from src.gateway.server import Gateway

    app = Gateway().app
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
    api = "/" + "api"
    settings = api + "/" + "settings"

    removed = {
        ("POST", api + "/" + "test"),
        ("GET", settings),
        ("POST", settings),
        ("GET", settings + "/" + "providers"),
        ("GET", settings + "/" + "ollama" + "/" + "models"),
        ("POST", settings + "/" + "ollama" + "/" + "pull"),
    }

    assert removed.isdisjoint(routes)
