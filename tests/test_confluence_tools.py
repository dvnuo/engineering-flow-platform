import pytest

from tests._lightweight_runtime_loaders import (
    load_confluence_init_lightweight,
    load_root_execute_tool_lightweight,
)


def test_confluence_tools_schemas_contract():
    module, cleanup = load_confluence_init_lightweight()
    try:
        schemas = module.get_tools_schemas()
        by_name = {s["function"]["name"]: s for s in schemas}

        assert "confluence_get_page" in by_name
        assert "confluence_get_page_by_url" in by_name
        assert "confluence_prepare_page_context" in by_name

        assert "max_chars" not in by_name["confluence_get_page"]["function"]["parameters"]["properties"]
        assert "max_chars" not in by_name["confluence_get_page_by_url"]["function"]["parameters"]["properties"]
        assert by_name["confluence_prepare_page_context"]["function"]["parameters"]["properties"]["include_children"]["default"] is True

        names = set(by_name)
        assert "confluence_get_page_preview" not in names
        assert "confluence_get_page_by_url_preview" not in names
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_confluence_prepare_page_context_manifest_contract(monkeypatch):
    module, cleanup = load_confluence_init_lightweight()
    try:
        async def _fake_prepare(**kwargs):
            return {
                "bundle": {"completeness_ledger": {"source_complete": True}},
                "manifest": {
                    "source_complete": True,
                    "comments_loaded": "120/120",
                    "descendants_complete": True,
                    "source_tree_complete": True,
                    "context_ref": "ctx://context/s-conf/source",
                    "digest_ref": "ctx://context/s-conf/digest",
                },
                "artifact_refs": [{"artifact_id": "a1"}],
            }

        monkeypatch.setattr(module, "prepare_confluence_page_source", _fake_prepare)
        monkeypatch.setattr(module, "format_confluence_source_manifest", lambda prepared: "\n".join([
            "[confluence source bundle prepared]",
            f"source_complete: {prepared['manifest']['source_complete']}",
            f"comments_loaded: {prepared['manifest']['comments_loaded']}",
            f"descendants_complete: {prepared['manifest']['descendants_complete']}",
            f"source_tree_complete: {prepared['manifest']['source_tree_complete']}",
            f"context_ref: {prepared['manifest']['context_ref']}",
        ]))

        out = await module.confluence_prepare_page_context("123", _session_id="s-conf")
        assert "[confluence source bundle prepared]" in out
        assert "source_complete: True" in out
        assert "comments_loaded: 120/120" in out
        assert "descendants_complete: True" in out
        assert "source_tree_complete: True" in out
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_confluence_get_page_defaults_include_children(monkeypatch):
    module, cleanup = load_confluence_init_lightweight()
    try:
        captured = {}

        async def _fake_prepare(**kwargs):
            captured.update(kwargs)
            return "[confluence source bundle prepared]"

        monkeypatch.setattr(module, "confluence_prepare_page_context", _fake_prepare)
        out = await module.confluence_get_page("123", _session_id="s1")

        assert "[confluence source bundle prepared]" in out
        assert captured["include_children"] is True
        assert captured["_session_id"] == "s1"
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_confluence_get_comments_and_children_bounded_contract(monkeypatch):
    module, cleanup = load_confluence_init_lightweight()
    try:
        class _Channel:
            def is_configured(self):
                return True

            async def get_all_comments_with_ledger(self, page_id, limit=100):
                return [{"id": "1", "body": {"storage": {"value": "secret"}}}], {"loaded": 1, "total": 1, "complete": True}

            async def get_all_page_children_with_ledger(self, page_id, limit=100):
                return [{"id": "c1", "title": "Child"}], {"loaded": 1, "total": 1, "complete": True}

        monkeypatch.setattr(module, "confluence_channel", _Channel())
        monkeypatch.setattr(module, "put_text", lambda **kwargs: f"ctx://context/{kwargs['session_id']}/blob")

        comments = await module.confluence_get_comments("42", _session_id="sess")
        assert "[confluence comments prepared]" in comments
        assert "comments_loaded: 1/1" in comments
        assert "comments_complete: True" in comments
        assert "comments_preview: omitted" in comments

        children = await module.confluence_get_page_children("42", _session_id="sess")
        assert "[confluence children prepared]" in children
        assert "children_complete: True" in children
        assert "children_preview: omitted" in children
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_execute_tool_confluence_dispatch_passes_session_id(monkeypatch):
    root, cleanup = load_root_execute_tool_lightweight()
    try:
        captured = {}

        async def _fake_get_page_by_url(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return "[confluence source bundle prepared]\ncontext_ref: ctx://context/s1/k"

        async def _fake_get_children(page_id, limit=10, _session_id=None):
            captured["children_session"] = _session_id
            return "[confluence children prepared]\ncontext_ref: ctx://context/s1/children"

        monkeypatch.setattr(root.confluence, "confluence_get_page_by_url", _fake_get_page_by_url)
        monkeypatch.setattr(root.confluence, "confluence_get_page_children", _fake_get_children)

        page_result = await root.execute_tool("confluence_get_page_by_url", url="https://wiki.local/pages/123/Title", _session_id="s1")
        assert page_result.success is True
        assert captured["_session_id"] == "s1"
        assert captured["url"].startswith("https://wiki.local/pages/")

        children_result = await root.execute_tool("confluence_get_page_children", page_id="123", _session_id="s1")
        assert children_result.success is True
        assert captured["children_session"] == "s1"
    finally:
        cleanup()


def test_root_execute_tool_lightweight_cleanup_preserves_src_runtime_parent_attr():
    import sys
    import src
    import src.runtime.execution_bus  # noqa: F401

    assert hasattr(src, "runtime")
    original_src = sys.modules.get("src")
    original_runtime = sys.modules.get("src.runtime")

    root, cleanup = load_root_execute_tool_lightweight()
    try:
        assert root is not original_src
    finally:
        cleanup()

    restored_src = sys.modules.get("src")
    assert restored_src is original_src
    assert sys.modules.get("src.runtime") is original_runtime
    assert getattr(restored_src, "runtime", None) is original_runtime
