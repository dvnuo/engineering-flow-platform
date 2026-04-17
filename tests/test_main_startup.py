import importlib


def test_main_imports_with_automation_watchers():
    main_module = importlib.import_module("main")

    assert hasattr(main_module, "start_automation_watchers")
    assert hasattr(main_module, "stop_automation_watchers")
    assert hasattr(main_module, "are_automation_watchers_enabled")
