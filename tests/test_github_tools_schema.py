from src.github import get_tools_schemas


def test_github_tool_schema_names_are_unique():
    schemas = get_tools_schemas()
    names = [tool["function"]["name"] for tool in schemas]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert duplicates == []
