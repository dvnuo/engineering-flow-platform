from pathlib import Path

from src.efp_runtime.session.gateway_facade import runtime_session_root


def test_runtime_session_root_prefers_explicit_argument(monkeypatch, tmp_path):
    explicit_root = tmp_path / "explicit-root"
    env_root = tmp_path / "env-root"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("EFP_RUNTIME_SESSION_ROOT", str(env_root))
    monkeypatch.setenv("EFP_WORKSPACE_DIR", str(workspace))

    assert runtime_session_root(explicit_root) == explicit_root.resolve()


def test_runtime_session_root_prefers_env_root_over_workspace(monkeypatch, tmp_path):
    env_root = tmp_path / "env-root"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("EFP_RUNTIME_SESSION_ROOT", str(env_root))
    monkeypatch.setenv("EFP_WORKSPACE_DIR", str(workspace))

    assert runtime_session_root() == env_root.resolve()


def test_runtime_session_root_falls_back_to_workspace_dir(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    monkeypatch.delenv("EFP_RUNTIME_SESSION_ROOT", raising=False)
    monkeypatch.setenv("EFP_WORKSPACE_DIR", str(workspace))

    assert runtime_session_root() == (workspace / ".efp" / "runtime").resolve()


def test_runtime_session_root_ignores_blank_env_values(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("EFP_RUNTIME_SESSION_ROOT", "   ")
    monkeypatch.setenv("EFP_WORKSPACE_DIR", "   ")
    monkeypatch.setenv("HOME", str(home))

    assert runtime_session_root() == (Path.home() / ".efp" / "runtime").resolve()
