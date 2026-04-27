import pytest

from tests._lightweight_runtime_loaders import (
    load_jira_init_lightweight,
    load_root_execute_tool_lightweight,
)


def test_jira_tools_schemas_contract():
    module, cleanup = load_jira_init_lightweight()
    try:
        schemas = module.get_tools_schemas()
        by_name = {s["function"]["name"]: s for s in schemas}

        assert "jira_get_issue" in by_name
        assert "jira_get_issue_by_url" in by_name
        assert "jira_prepare_issue_context" in by_name
        assert "jira_get_comments" in by_name

        assert "max_chars" not in by_name["jira_get_issue"]["function"]["parameters"]["properties"]
        assert "max_comments" not in by_name["jira_get_issue"]["function"]["parameters"]["properties"]
        assert "max_chars" not in by_name["jira_get_issue_by_url"]["function"]["parameters"]["properties"]
        assert "max_comments" not in by_name["jira_get_issue_by_url"]["function"]["parameters"]["properties"]

        names = set(by_name)
        assert "jira_get_issue_preview" not in names
        assert "jira_get_issue_by_url_preview" not in names
        assert "export_issues_to_markdown" not in names
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_jira_prepare_issue_context_manifest_contract(monkeypatch):
    module, cleanup = load_jira_init_lightweight()
    try:
        class _Prepared:
            manifest = {
                "context_ref": "ctx://context/s-jira/source",
                "digest_ref": "ctx://context/s-jira/digest",
                "source_complete": True,
                "source_complete_for_generation": True,
                "comments_loaded": "20/20",
            }

        async def _fake_prepare(**kwargs):
            return _Prepared()

        monkeypatch.setattr(module, "prepare_jira_issue_source", _fake_prepare)
        monkeypatch.setattr(module, "format_jira_source_manifest", lambda prepared: "\n".join([
            "[jira source bundle prepared]",
            f"source_complete: {prepared.manifest['source_complete']}",
            f"source_complete_for_generation: {prepared.manifest['source_complete_for_generation']}",
            f"comments_loaded: {prepared.manifest['comments_loaded']}",
            f"context_ref: {prepared.manifest['context_ref']}",
        ]))

        out = await module.jira_prepare_issue_context("PROJ-1", _session_id="s-jira")
        assert "[jira source bundle prepared]" in out
        assert "source_complete: True" in out
        assert "source_complete_for_generation: True" in out
        assert "comments_loaded: 20/20" in out
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_jira_get_issue_by_url_defaults_to_source_complete_path(monkeypatch):
    module, cleanup = load_jira_init_lightweight()
    try:
        captured = {}

        async def _fake_prepare(**kwargs):
            captured.update(kwargs)
            return "[jira source bundle prepared]"

        monkeypatch.setattr(module, "jira_prepare_issue_context", _fake_prepare)
        out = await module.jira_get_issue_by_url("https://jira.local/browse/PROJ-1", _session_id="s1")

        assert "[jira source bundle prepared]" in out
        assert captured["issue_key_or_url"].endswith("/PROJ-1")
        assert captured["_session_id"] == "s1"
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_jira_get_comments_bounded_manifest(monkeypatch):
    module, cleanup = load_jira_init_lightweight()
    try:
        class _Adapter:
            async def get_issue(self, **kwargs):
                return {"fields": {"comment": {"comments": [{"id": "1", "body": "A" * 5000}], "total": 1}}}

            def _get_comments_list(self, issue, max_comments):
                return [{"id": "1", "body": "A" * 5000}]

        monkeypatch.setattr(module, "_get_adapter", lambda: _Adapter())
        monkeypatch.setattr(module, "jira_channel", type("C", (), {"is_configured": lambda self: True})())
        monkeypatch.setattr(module, "put_text", lambda **kwargs: f"ctx://context/{kwargs['session_id']}/blob")

        out = await module.jira_get_comments("PROJ-7", _session_id="s-jira-comments")
        assert "[jira comments bundle prepared]" in out
        assert "context_ref: ctx://context/s-jira-comments/" in out
        assert "comments_loaded: 1/1" in out
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_execute_tool_jira_dispatch_and_export_defaults(monkeypatch):
    root, cleanup = load_root_execute_tool_lightweight()
    try:
        captured = {}

        async def _fake_get_issue_by_url(url, **kwargs):
            captured["jira_session"] = kwargs.get("_session_id")
            return "[jira source bundle prepared]\ncontext_ref: ctx://context/s1/k"

        async def _fake_get_comments(issue_key, _session_id=None):
            captured["comments_session"] = _session_id
            return "[jira comments bundle prepared]\ncontext_ref: ctx://context/s1/comments"

        async def _fake_export(**kwargs):
            captured["export"] = kwargs
            return {"success": True, "status": "success"}

        monkeypatch.setattr(root.jira, "jira_get_issue_by_url", _fake_get_issue_by_url)
        monkeypatch.setattr(root.jira, "jira_get_comments", _fake_get_comments)
        monkeypatch.setattr(root.jira, "jira_export_issues_to_markdown", _fake_export)

        by_url = await root.execute_tool("jira_get_issue_by_url", url="https://jira.local/browse/PROJ-1", _session_id="s1")
        assert by_url.success is True
        assert captured["jira_session"] == "s1"

        comments = await root.execute_tool("jira_get_comments", issue_key="PROJ-1", _session_id="s1")
        assert comments.success is True
        assert captured["comments_session"] == "s1"

        exported = await root.execute_tool(
            "jira_export_issues_to_markdown",
            input="MMGFX-14839",
            page_size=None,
            max_issues=None,
            output_mode=None,
            attachments_dir=None,
            include_raw_snapshot=None,
            include_coverage_ledger=None,
            comments_order=None,
            attachments_concurrency=None,
            attachments_max_size=None,
            attachments_inline_text_threshold=None,
            attachments_retries=None,
            attachments_backoff=None,
            attachments_preserve_binary=None,
        )
        assert exported.success is True
        assert captured["export"]["page_size"] == 50
        assert captured["export"]["max_issues"] == 100
        assert captured["export"]["output_mode"] == "auto"
        assert captured["export"]["attachments_dir"] == "attachments"
        assert captured["export"]["include_raw_snapshot"] is False
        assert captured["export"]["include_coverage_ledger"] is True
        assert captured["export"]["comments_order"] == "latest_first"
    finally:
        cleanup()
