import importlib

import pytest


OPENCODE_CORE_TOOLS = {
    "apply_patch",
    "bash",
    "edit",
    "glob",
    "grep",
    "read",
    "todowrite",
    "webfetch",
    "write",
}

LEGACY_PYTHON_TOOLS = {
    "jira_get_issue",
    "github_get_pr",
    "confluence_get_page",
    "git_clone",
    "run_command",
    "list_dir",
}


def test_src_tool_surface_is_runtime_v2_opencode_builtin_only():
    import src

    names = set(src.get_tool_names())
    assert OPENCODE_CORE_TOOLS.issubset(names)
    assert LEGACY_PYTHON_TOOLS.isdisjoint(names)


def test_src_tool_schema_uses_openai_function_shape_and_runtime_metadata():
    import src

    schemas = src.get_tools_schema()
    assert schemas
    for schema in schemas:
        assert schema["type"] == "function"
        assert schema["function"]["name"] in src.get_tool_names()
        assert isinstance(schema["function"]["description"], str)
        assert isinstance(schema["function"]["parameters"], dict)
        assert schema["metadata"]["tool_source"] == "efp_runtime"
        assert schema["metadata"]["tool_id"] == schema["function"]["name"]


def test_removed_python_tool_packages_are_not_importable():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.bash_tools")

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.tools_external")
