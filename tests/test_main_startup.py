import importlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path, PurePosixPath

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def main_module():
    return importlib.import_module("main")


@pytest.fixture
def debug_config(main_module):
    """Swap main's debug config dict and restore it afterwards."""
    original = main_module.config._config.get("debug")

    def _set(value):
        main_module.config._config["debug"] = value

    yield _set

    if original is None:
        main_module.config._config.pop("debug", None)
    else:
        main_module.config._config["debug"] = original


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


def test_main_source_does_not_reference_automation_watcher_lifecycle_calls():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "start_automation_watchers(" not in source
    assert "stop_automation_watchers(" not in source
    assert "are_automation_watchers_enabled" not in source


class TestLogLevelResolution:
    """EFP_LOG_LEVEL > config.debug.log_level > debug.enabled."""

    def test_env_override_wins_over_config(self, main_module, debug_config, monkeypatch):
        debug_config({"enabled": False, "log_level": "WARNING"})
        monkeypatch.setenv("EFP_LOG_LEVEL", "debug")

        assert main_module.resolve_log_level() == "DEBUG"

    def test_config_log_level_used_without_env(self, main_module, debug_config, monkeypatch):
        debug_config({"enabled": True, "log_level": "warning"})
        monkeypatch.delenv("EFP_LOG_LEVEL", raising=False)

        assert main_module.resolve_log_level() == "WARNING"

    def test_debug_enabled_implies_debug_level(self, main_module, debug_config, monkeypatch):
        debug_config({"enabled": True})
        monkeypatch.delenv("EFP_LOG_LEVEL", raising=False)

        assert main_module.resolve_log_level() == "DEBUG"

    def test_default_is_info(self, main_module, debug_config, monkeypatch):
        debug_config({})
        monkeypatch.delenv("EFP_LOG_LEVEL", raising=False)

        assert main_module.resolve_log_level() == "INFO"

    def test_blank_env_falls_through_to_config(self, main_module, debug_config, monkeypatch):
        debug_config({"enabled": False, "log_level": "ERROR"})
        monkeypatch.setenv("EFP_LOG_LEVEL", "   ")

        assert main_module.resolve_log_level() == "ERROR"


class TestLogDestination:
    """Log files must default off the (network) workspace volume."""

    def test_default_log_dir_is_absolute_and_off_workspace(self, main_module):
        # POSIX semantics: the container runs Linux even when tests run on Windows.
        assert main_module.DEFAULT_LOG_DIR == "/app/logs"
        assert PurePosixPath(main_module.DEFAULT_LOG_DIR).is_absolute()
        assert not main_module.DEFAULT_LOG_DIR.startswith("/workspace")

    def test_setup_logging_uses_env_log_dir(self, main_module, monkeypatch, tmp_path):
        recorded = {}

        def fake_setup_logging(**kwargs):
            recorded.update(kwargs)
            return sys.modules["logging"].getLogger("main-startup-test")

        monkeypatch.setattr(main_module, "setup_logging", fake_setup_logging)
        monkeypatch.setenv("EFP_LOG_DIR", str(tmp_path / "podlogs"))
        monkeypatch.setenv("EFP_LOG_LEVEL", "DEBUG")

        main_module.setup_logging_config()

        assert recorded["log_dir"] == str(tmp_path / "podlogs")
        assert recorded["level"] == "DEBUG"

    def test_setup_logging_defaults_to_the_absolute_log_dir(
        self, main_module, monkeypatch
    ):
        """Without EFP_LOG_DIR the sink is the absolute default, never "logs"."""
        recorded = {}

        def fake_setup_logging(**kwargs):
            recorded.update(kwargs)
            return sys.modules["logging"].getLogger("main-startup-test")

        monkeypatch.setattr(main_module, "setup_logging", fake_setup_logging)
        monkeypatch.delenv("EFP_LOG_DIR", raising=False)

        main_module.setup_logging_config()

        assert recorded["log_dir"] == main_module.DEFAULT_LOG_DIR
        assert recorded["log_dir"] != "logs"
        # POSIX semantics: the container runs Linux even when tests run on
        # Windows, and a relative dir would land on the workspace volume.
        assert PurePosixPath(recorded["log_dir"]).is_absolute()


class TestDebugCliOverrides:
    """CLI flags merge into the debug config instead of replacing it."""

    def test_debug_flag_keeps_sibling_debug_keys(self, main_module):
        merged = main_module.merge_debug_cli_overrides(
            {"log_level": "WARNING", "extra": "keep"},
            debug=True,
            httpx_trace=False,
        )

        assert merged["log_level"] == "WARNING"
        assert merged["extra"] == "keep"
        assert merged["enabled"] is True
        assert merged["httpx_trace"] is False

    def test_httpx_trace_flag_alone_keeps_enabled_and_level(self, main_module):
        merged = main_module.merge_debug_cli_overrides(
            {"enabled": False, "log_level": "ERROR"},
            debug=False,
            httpx_trace=True,
        )

        assert merged == {
            "enabled": False,
            "log_level": "ERROR",
            "httpx_trace": True,
        }

    def test_no_flags_leaves_the_config_untouched(self, main_module):
        original = {"enabled": False, "log_level": "ERROR"}
        merged = main_module.merge_debug_cli_overrides(
            original, debug=False, httpx_trace=False
        )

        assert merged == original
        assert merged is not original

    def test_missing_debug_section_is_tolerated(self, main_module):
        assert main_module.merge_debug_cli_overrides(
            None, debug=True, httpx_trace=True
        ) == {"enabled": True, "httpx_trace": True}

    def test_debug_flag_does_not_reset_a_configured_log_level(
        self, main_module, debug_config, monkeypatch
    ):
        """The regression this guards: --debug used to wipe log_level."""
        monkeypatch.delenv("EFP_LOG_LEVEL", raising=False)
        debug_config(
            main_module.merge_debug_cli_overrides(
                {"log_level": "WARNING"}, debug=True, httpx_trace=False
            )
        )

        assert main_module.resolve_log_level() == "WARNING"


class TestLoggingSetupOrder:
    """Logging must be configured before Gateway() is built at import time."""

    def test_logging_is_configured_before_gateway_import(self, tmp_path):
        """Import main in a subprocess and watch when the sinks come up.

        A sentinel is logged the moment ``src.gateway.server`` starts importing
        (Gateway() runs at import time). If logging were configured after that
        import, neither the sentinel nor the gateway's own import-time lines
        would reach stdout at all.
        """
        probe = textwrap.dedent(
            """
            import importlib.abc
            import logging
            import sys


            class _GatewayImportProbe(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "src.gateway.server":
                        logging.getLogger(fullname).info(
                            "SENTINEL_GATEWAY_IMPORT_BEGIN"
                        )
                    return None


            sys.meta_path.insert(0, _GatewayImportProbe())
            import main  # noqa: F401
            """
        )
        env = dict(os.environ)
        env["EFP_LOG_DIR"] = str(tmp_path / "logs")
        env.pop("EFP_LOG_TO_FILE", None)

        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

        assert result.returncode == 0, result.stderr
        stdout = result.stdout
        assert "Engineering Flow Platform - Logging Initialized" in stdout
        # Emitted while src.gateway.server was being imported: proves handlers
        # were already attached at that point.
        assert "SENTINEL_GATEWAY_IMPORT_BEGIN" in stdout
        # The gateway's own import-time logging is captured too.
        assert "Runtime API routes registered" in stdout
        assert stdout.index("Engineering Flow Platform - Logging Initialized") < (
            stdout.index("SENTINEL_GATEWAY_IMPORT_BEGIN")
        )
        assert stdout.index("SENTINEL_GATEWAY_IMPORT_BEGIN") < (
            stdout.index("Runtime API routes registered")
        )


class TestRuntimePathsBootLine:
    def test_log_runtime_paths_emits_one_line_with_every_root(self, main_module, caplog, tmp_path):
        import logging

        caplog.set_level(logging.INFO)
        main_module.log_runtime_paths(logging.getLogger("main-paths-test"), tmp_path)

        lines = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("runtime.paths")
        ]

        assert len(lines) == 1
        line = lines[0]
        assert "\n" not in line
        for field in (
            "workspace_root=",
            "session_root=",
            "project_skills_dir=",
            "user_skills_dir=",
            "snapshot_storage_root=",
            "log_dir=",
            "cwd=",
        ):
            assert field in line
        assert f"workspace_root={tmp_path}" in line
        assert str(Path(tmp_path) / ".efp_runtime" / "workspace_snapshots") in line
