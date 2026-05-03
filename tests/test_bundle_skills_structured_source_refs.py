import importlib.util
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_shared_loader_module_with_jira_confluence_stubs():
    src_pkg = sys.modules.get("src")
    created_src_stub = False
    if src_pkg is None:
        src_pkg = types.ModuleType("src")
        src_pkg.__path__ = []
        created_src_stub = True
    jira_pkg = types.ModuleType("src.jira")
    jira_pkg.__path__ = []
    confluence_pkg = types.ModuleType("src.confluence")
    confluence_pkg.__path__ = []
    runtime_pkg = types.ModuleType("src.runtime")
    runtime_pkg.__path__ = []

    jira_service = types.ModuleType("src.jira.source_service")
    jira_service.prepare_jira_issue_source = lambda source, session_id=None: None
    jira_service.format_jira_source_manifest = lambda prepared: "jira-manifest"

    confluence_service = types.ModuleType("src.confluence.source_service")
    confluence_service.prepare_confluence_page_source = lambda source, session_id=None: None
    confluence_service.format_confluence_source_manifest = lambda prepared: "confluence-manifest"
    runtime_assets = types.ModuleType("src.runtime.requirement_bundle_assets")
    runtime_assets.parse_bundle_ref = lambda ref: SimpleNamespace(owner="", repo="", branch="", path="")

    async def _unused_prepare_github_doc_source(raw, default_ref, session_id=None):
        return {
            "content_text": "",
            "artifact_refs": [],
            "context_ref": None,
            "digest_ref": None,
        }

    runtime_assets.prepare_github_doc_source = _unused_prepare_github_doc_source

    modules = {
        "src.jira": jira_pkg,
        "src.jira.source_service": jira_service,
        "src.confluence": confluence_pkg,
        "src.confluence.source_service": confluence_service,
        "src.runtime": runtime_pkg,
        "src.runtime.requirement_bundle_assets": runtime_assets,
    }
    if created_src_stub:
        modules["src"] = src_pkg
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    src_pkg.jira = jira_pkg
    src_pkg.confluence = confluence_pkg
    src_pkg.runtime = runtime_pkg
    jira_pkg.source_service = jira_service
    confluence_pkg.source_service = confluence_service
    runtime_pkg.requirement_bundle_assets = runtime_assets
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
        restored_src = sys.modules.get("src")
        restored_runtime = sys.modules.get("src.runtime")
        if restored_src is not None and restored_runtime is None:
            try:
                restored_runtime = importlib.import_module("src.runtime")
            except Exception:
                restored_runtime = None
        if restored_src is not None and restored_runtime is not None:
            setattr(restored_src, "runtime", restored_runtime)
        if restored_src is not None and sys.modules.get("src.jira") is not None:
            setattr(restored_src, "jira", sys.modules["src.jira"])
        if restored_src is not None and sys.modules.get("src.confluence") is not None:
            setattr(restored_src, "confluence", sys.modules["src.confluence"])
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

        monkeypatch.setattr(module, "prepare_jira_issue_source", _fake_prepare_jira)
        monkeypatch.setattr(module, "prepare_confluence_page_source", _fake_prepare_confluence)

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
