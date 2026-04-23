def test_load_module_from_repo_path_keeps_gateway_importable():
    from tests._import_helpers import load_module_from_repo_path

    load_module_from_repo_path("src.gateway.event_bus", "src/gateway/event_bus.py")

    import src.gateway.server as gateway_server
    from src.gateway import webchat

    assert gateway_server is not None
    assert webchat is not None


def test_load_module_from_repo_path_sets_real_package_paths():
    import sys
    from tests._import_helpers import load_module_from_repo_path

    load_module_from_repo_path("src.gateway.event_bus", "src/gateway/event_bus.py")

    assert "src" in sys.modules
    assert "src.gateway" in sys.modules
    assert getattr(sys.modules["src"], "__path__", None)
    assert getattr(sys.modules["src.gateway"], "__path__", None)
