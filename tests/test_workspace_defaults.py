from pathlib import Path

import pytest

from src.workspace_defaults import DEFAULT_RUNTIME_WORKSPACE, resolve_runtime_workspace


@pytest.mark.parametrize(
    "config_data",
    [
        None,
        {},
        {"workspace": {"path": None}},
        {"workspace": {"path": ""}},
        {"workspace": ""},
    ],
)
def test_resolve_runtime_workspace_defaults_to_runtime_workspace(config_data):
    assert resolve_runtime_workspace(config_data) == DEFAULT_RUNTIME_WORKSPACE


@pytest.mark.parametrize(
    "legacy_path",
    [
        "~/.efp/workspace",
        "~/.efp/workspace/",
        Path.home() / ".efp" / "workspace",
        "/root/.efp/workspace",
    ],
)
def test_resolve_runtime_workspace_treats_legacy_default_as_alias(legacy_path):
    assert (
        resolve_runtime_workspace({"workspace": {"path": legacy_path}})
        == DEFAULT_RUNTIME_WORKSPACE
    )


def test_resolve_runtime_workspace_preserves_custom_override(tmp_path):
    custom_workspace = tmp_path / "custom-workspace"

    assert (
        resolve_runtime_workspace({"workspace": {"path": str(custom_workspace)}})
        == custom_workspace
    )
