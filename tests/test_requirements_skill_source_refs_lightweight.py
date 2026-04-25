import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_shared_loader_module_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []
    jira_pkg = types.ModuleType("src.jira")
    jira_pkg.__path__ = []
    confluence_pkg = types.ModuleType("src.confluence")
    confluence_pkg.__path__ = []
    runtime_pkg = types.ModuleType("src.runtime")
    runtime_pkg.__path__ = []

    jira_service = types.ModuleType("src.jira.source_service")
    jira_service.format_jira_source_manifest = lambda prepared: "jira-manifest"
    jira_service.prepare_jira_issue_source = None

    confluence_service = types.ModuleType("src.confluence.source_service")
    confluence_service.format_confluence_source_manifest = lambda prepared: "confluence-manifest"
    confluence_service.prepare_confluence_page_source = None

    rb_assets = types.ModuleType("src.runtime.requirement_bundle_assets")
    rb_assets.parse_bundle_ref = lambda bundle_ref: types.SimpleNamespace(owner="acme", repo="repo", path="bundles/a", branch="main")
    rb_assets.prepare_github_doc_source = None

    modules = {
        "src": src_pkg,
        "src.jira": jira_pkg,
        "src.jira.source_service": jira_service,
        "src.confluence": confluence_pkg,
        "src.confluence.source_service": confluence_service,
        "src.runtime": runtime_pkg,
        "src.runtime.requirement_bundle_assets": rb_assets,
    }
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    src_pkg.jira = jira_pkg
    src_pkg.confluence = confluence_pkg
    src_pkg.runtime = runtime_pkg
    jira_pkg.source_service = jira_service
    confluence_pkg.source_service = confluence_service
    runtime_pkg.requirement_bundle_assets = rb_assets
    spec = importlib.util.spec_from_file_location("skills.shared_bundle_source_loaders", Path("skills/shared_bundle_source_loaders.py"))
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
async def test_skill_source_loaders_return_source_refs_lightweight(monkeypatch):
    module, cleanup = _load_shared_loader_module_lightweight()
    try:
        class _JiraPrepared:
            issue_key = "P-1"
            bundle = {"artifact_refs": [{"artifact_id": "jira-a1"}]}
            manifest = {"context_ref": "jira-ctx", "digest_ref": "jira-dig"}

        async def _fake_prepare_jira(source, session_id=None):
            return _JiraPrepared()

        async def _fake_prepare_conf(source, session_id=None):
            return {
                "page_id": "42",
                "manifest": {"context_ref": "conf-ctx", "digest_ref": "conf-dig"},
                "artifact_refs": [{"artifact_id": "conf-a1"}],
            }

        class _DocRef:
            owner = "acme"
            repo = "repo"
            branch = "main"
            path = "docs/spec.md"

        async def _fake_prepare_github(raw, default_ref, session_id=None):
            return {
                "doc_ref": _DocRef(),
                "bundle": {},
                "context_ref": "gh-ctx",
                "digest_ref": "gh-dig",
                "artifact_refs": [{"artifact_id": "gh-a1"}],
                "content_text": "hello",
            }

        monkeypatch.setattr("src.jira.source_service.prepare_jira_issue_source", _fake_prepare_jira)
        monkeypatch.setattr("src.confluence.source_service.prepare_confluence_page_source", _fake_prepare_conf)
        monkeypatch.setattr("src.runtime.requirement_bundle_assets.prepare_github_doc_source", _fake_prepare_github)

        jira_items = await module._load_jira_sources(["P-1"], session_id="s1")
        conf_items = await module._load_confluence_sources(["42"], session_id="s1")
        gh_items = await module._load_github_doc_sources({"repo": "acme/repo", "path": "bundles/a", "branch": "main"}, ["docs/spec.md"], session_id="s1")

        assert jira_items[0]["artifact_refs"] == [{"artifact_id": "jira-a1"}]
        assert jira_items[0]["context_ref"] == "jira-ctx"
        assert jira_items[0]["digest_ref"] == "jira-dig"

        assert conf_items[0]["artifact_refs"] == [{"artifact_id": "conf-a1"}]
        assert conf_items[0]["context_ref"] == "conf-ctx"
        assert conf_items[0]["digest_ref"] == "conf-dig"

        assert gh_items[0]["artifact_refs"] == [{"artifact_id": "gh-a1"}]
        assert gh_items[0]["context_ref"] == "gh-ctx"
        assert gh_items[0]["digest_ref"] == "gh-dig"
    finally:
        cleanup()
