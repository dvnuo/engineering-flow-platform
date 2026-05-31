from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from efp_runtime.runtime import RuntimeConfig
from efp_runtime.tools.builtin import create_core_tool_registry


REMOVED_TOOL_IDS = {
    "fetch",
    "list_dir",
    "read_file",
    "shell_exec",
    "shell_kill",
    "shell_status",
    "skill_list",
    "task_cancel",
    "task_status",
    "todo_write",
    "write_file",
}


@pytest.mark.parametrize(
    "module_name",
    [
        "src.context_tools",
        "src.efp_runtime.tools.local",
        "src.efp_runtime.tools.external",
        "efp_runtime.tools.local",
        "efp_runtime.tools.external",
    ],
)
def test_removed_python_tool_compat_modules_are_not_importable(module_name: str):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"include_legacy_tool_aliases": True},
        {"tool_surface": "legacy"},
        {"enable_local_python_tools": True},
        {"local_tool_directories": [Path("tools")]},
        {"enable_skill_list_tool": True},
    ],
)
def test_runtime_config_rejects_removed_tool_compat_options(kwargs):
    with pytest.raises(TypeError):
        RuntimeConfig(**kwargs)


def test_core_registry_does_not_expose_removed_tool_ids(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)

    assert REMOVED_TOOL_IDS.isdisjoint(registry.ids())
    for tool_id in REMOVED_TOOL_IDS:
        assert registry.get(tool_id) is None
