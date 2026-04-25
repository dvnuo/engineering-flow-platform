import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_skill_module_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    agents_pkg = types.ModuleType("src.agents")
    agents_pkg.__path__ = []
    agents_exec = types.ModuleType("src.agents.executor")

    class SkillResult:
        def __init__(self, success, output=None, data=None, error=None):
            self.success = success
            self.output = output
            self.data = data
            self.error = error

    def skill(**_kwargs):
        def _decorator(fn):
            return fn
        return _decorator

    agents_exec.SkillResult = SkillResult
    agents_exec.skill = skill

    agents_llm = types.ModuleType("src.agents.llm")
    agents_llm.LLMClient = object

    conf_service = types.ModuleType("src.confluence.source_service")
    conf_service.format_confluence_source_manifest = lambda prepared: "conf-manifest"
    conf_service.prepare_confluence_page_source = None

    jira_service = types.ModuleType("src.jira.source_service")
    jira_service.format_jira_source_manifest = lambda prepared: "jira-manifest"
    jira_service.prepare_jira_issue_source = None

    github_mod = types.ModuleType("src.github")
    github_mod.github_channel = types.SimpleNamespace(is_configured=lambda: True)

    rb_assets = types.ModuleType("src.runtime.requirement_bundle_assets")
    rb_assets.RequirementBundleError = ValueError
    rb_assets.load_bundle_manifest = None
    rb_assets.parse_bundle_ref = lambda bundle_ref: types.SimpleNamespace(owner="acme", repo="repo", path="bundles/a", branch="main")
    rb_assets.prepare_github_doc_source = None
    rb_assets.resolve_bundle_links = None
    rb_assets.resolve_target_bundle_ref = None
    rb_assets.write_requirements_doc_for_ref = None

    redaction_mod = types.ModuleType("src.utils.redaction")
    redaction_mod.sanitize_exception_message = lambda exc: str(exc)

    modules = {
        "src": src_pkg,
        "src.agents": agents_pkg,
        "src.agents.executor": agents_exec,
        "src.agents.llm": agents_llm,
        "src.confluence.source_service": conf_service,
        "src.jira.source_service": jira_service,
        "src.github": github_mod,
        "src.runtime.requirement_bundle_assets": rb_assets,
        "src.utils.redaction": redaction_mod,
    }
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location("skills.collect_requirements_to_bundle.skill", Path("skills/collect_requirements_to_bundle/skill.py"))
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


@pytest.mark.asyncio
async def test_skill_source_loaders_return_source_refs_lightweight(monkeypatch):
    module = _load_skill_module_lightweight()

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

    monkeypatch.setattr(module, "prepare_jira_issue_source", _fake_prepare_jira)
    monkeypatch.setattr(module, "prepare_confluence_page_source", _fake_prepare_conf)
    monkeypatch.setattr(module, "prepare_github_doc_source", _fake_prepare_github)

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
