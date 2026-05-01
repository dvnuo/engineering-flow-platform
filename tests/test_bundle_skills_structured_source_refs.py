import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_shared_loader_module_with_jira_confluence_stubs():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []
    jira_pkg = types.ModuleType("src.jira")
    jira_pkg.__path__ = []
    confluence_pkg = types.ModuleType("src.confluence")
    confluence_pkg.__path__ = []

    jira_service = types.ModuleType("src.jira.source_service")
    jira_service.prepare_jira_issue_source = None
    jira_service.format_jira_source_manifest = lambda prepared: "jira-manifest"

    confluence_service = types.ModuleType("src.confluence.source_service")
    confluence_service.prepare_confluence_page_source = None
    confluence_service.format_confluence_source_manifest = lambda prepared: "confluence-manifest"

    modules = {
        "src": src_pkg,
        "src.jira": jira_pkg,
        "src.jira.source_service": jira_service,
        "src.confluence": confluence_pkg,
        "src.confluence.source_service": confluence_service,
    }
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    src_pkg.jira = jira_pkg
    src_pkg.confluence = confluence_pkg
    jira_pkg.source_service = jira_service
    confluence_pkg.source_service = confluence_service
    spec = importlib.util.spec_from_file_location("skills.shared_bundle_source_loaders", Path("tests/fixtures/skills/shared_bundle_source_loaders.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    def _cleanup():
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        sys.modules.pop("skills.shared_bundle_source_loaders", None)

    return module, _cleanup


@pytest.mark.asyncio
async def test_bundle_skill_loaders_return_structured_jira_and_confluence_sources(monkeypatch):
    module, cleanup = _load_shared_loader_module_with_jira_confluence_stubs()
    try:
        class _JiraPrepared:
            issue_key = "P-1"
            bundle = {"artifact_refs": [{"artifact_id": "jira-a1"}]}
            manifest = {"context_ref": "jira-ctx", "digest_ref": "jira-dig"}

        async def _fake_prepare_jira(source, session_id=None):
            return _JiraPrepared()

        async def _fake_prepare_confluence(source, session_id=None):
            return {
                "page_id": "42",
                "manifest": {"context_ref": "conf-ctx", "digest_ref": "conf-dig"},
                "artifact_refs": [{"artifact_id": "conf-a1"}],
            }

        monkeypatch.setattr("src.jira.source_service.prepare_jira_issue_source", _fake_prepare_jira)
        monkeypatch.setattr("src.confluence.source_service.prepare_confluence_page_source", _fake_prepare_confluence)

        jira_items = await module._load_jira_sources(["P-1"], session_id="s1")
        conf_items = await module._load_confluence_sources(["42"], session_id="s1")

        assert jira_items[0]["content"] == "jira-manifest"
        assert jira_items[0]["source_kind"] == "jira_issue"
        assert jira_items[0]["artifact_refs"] == [{"artifact_id": "jira-a1"}]
        assert jira_items[0]["context_ref"] == "jira-ctx"
        assert jira_items[0]["digest_ref"] == "jira-dig"

        assert conf_items[0]["content"] == "confluence-manifest"
        assert conf_items[0]["source_kind"] == "confluence_page"
        assert conf_items[0]["artifact_refs"] == [{"artifact_id": "conf-a1"}]
        assert conf_items[0]["context_ref"] == "conf-ctx"
        assert conf_items[0]["digest_ref"] == "conf-dig"
    finally:
        cleanup()
