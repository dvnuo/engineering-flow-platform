from pathlib import Path


def test_prepare_context_tools_are_exposed_and_return_manifest_string_contract():
    source = Path("src/github/__init__.py").read_text(encoding="utf-8")

    assert "async def github_prepare_issue_context(" in source
    assert "async def github_prepare_pr_context(" in source
    assert "format_github_source_manifest" in source
    assert '"name": "github_prepare_issue_context"' in source
    assert '"name": "github_prepare_pr_context"' in source


def test_legacy_github_prepare_tools_are_not_root_runtime_v2_dispatch_tools():
    source = Path("src/github/__init__.py").read_text(encoding="utf-8")
    assert "async def github_get_issue(" in source
    assert "async def github_get_pr(" in source
    assert "**State:**" in source

    dispatch = Path("src/__init__.py").read_text(encoding="utf-8")
    assert "github_prepare_issue_context" not in dispatch
    assert "github_prepare_pr_context" not in dispatch
