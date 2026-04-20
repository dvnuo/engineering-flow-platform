import importlib
import sys


def test_main_does_not_import_automation_watchers_symbols():
    main_module = importlib.import_module("main")

    assert not hasattr(main_module, "start_automation_watchers")
    assert not hasattr(main_module, "stop_automation_watchers")
    assert not hasattr(main_module, "are_automation_watchers_enabled")


def test_main_import_does_not_load_automation_watchers_module():
    if "src.cron.automation_watchers" in sys.modules:
        del sys.modules["src.cron.automation_watchers"]

    importlib.reload(importlib.import_module("main"))

    assert "src.cron.automation_watchers" not in sys.modules
