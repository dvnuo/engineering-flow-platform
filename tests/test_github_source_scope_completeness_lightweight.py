import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_github_source_service_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []
    github_pkg = types.ModuleType("src.github")
    github_pkg.__path__ = []

    file_artifacts_pkg = types.ModuleType("src.file_artifacts")
    file_artifacts_pkg.__path__ = []
    file_artifacts_pkg.can_project_to_text = lambda content_type, filename: str(content_type or "").startswith("text/")

    service_mod = types.ModuleType("src.file_artifacts.service")
    service_mod.attach_source_refs_to_artifact = lambda *args, **kwargs: None
    service_mod.bind_artifact_to_source_bundle = lambda *args, **kwargs: None
    service_mod.build_artifact_ref_dict = lambda record: {"artifact_id": getattr(record, "artifact_id", "a1")}
    service_mod.register_existing_file_as_artifact = lambda *args, **kwargs: types.SimpleNamespace(artifact_id="a1", file_id="a1")
    service_mod.update_projection_from_parse_result = lambda *args, **kwargs: None

    storage_mod = types.ModuleType("src.file_artifacts.storage")
    storage_mod.storage = types.SimpleNamespace(get_artifact=lambda artifact_id: types.SimpleNamespace(artifact_id=artifact_id), update_artifact_status=lambda *args, **kwargs: None)

    api_mod = types.ModuleType("src.github.api")

    class _Channel:
        async def get_issue(self, owner, repo, issue_number):
            return {"id": 1, "number": issue_number, "title": "I", "state": "open", "body": ""}

        async def get_issue_comments(self, owner, repo, issue_number):
            return []

        async def get_pull_request(self, owner, repo, pull_number):
            return {"id": 2, "number": pull_number, "title": "P", "state": "open", "body": ""}

        async def get_pr_comments(self, owner, repo, pull_number):
            return []

        async def get_file(self, owner, repo, path, branch):
            return {"content": "", "sha": "s", "size": 0}

    api_mod.github_channel = _Channel()

    links_mod = types.ModuleType("src.github.asset_links")
    links_mod.extract_github_asset_urls = lambda text: []

    doc_ref_mod = types.ModuleType("src.github.doc_refs")
    doc_ref_mod.parse_github_doc_ref = lambda raw, default_ref: default_ref

    source_context_mod = types.ModuleType("src.source_context")
    source_context_mod.persist_github_source_bundle_and_digest = lambda **kwargs: {"context_ref": "ctx://bundle", "digest_ref": "ctx://digest", "source_complete": True}

    scope_mod = types.ModuleType("src.source_bundle_completeness")
    spec_scope = importlib.util.spec_from_file_location("src.source_bundle_completeness", Path("src/source_bundle_completeness.py"))
    assert spec_scope and spec_scope.loader
    spec_scope.loader.exec_module(scope_mod)

    utils_pkg = types.ModuleType("src.utils")
    utils_pkg.__path__ = []
    attachment_mod = types.ModuleType("src.utils.attachment")
    attachment_mod.download_and_process_attachment = None
    parser_mod = types.ModuleType("src.utils.file_parser")
    parser_mod.parse_file = None
    parser_mod.save_uploaded_file = None

    modules = {
        "src": src_pkg,
        "src.github": github_pkg,
        "src.file_artifacts": file_artifacts_pkg,
        "src.file_artifacts.service": service_mod,
        "src.file_artifacts.storage": storage_mod,
        "src.github.api": api_mod,
        "src.github.asset_links": links_mod,
        "src.github.doc_refs": doc_ref_mod,
        "src.source_context": source_context_mod,
        "src.source_bundle_completeness": scope_mod,
        "src.utils": utils_pkg,
        "src.utils.attachment": attachment_mod,
        "src.utils.file_parser": parser_mod,
    }
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location("src.github.source_service", Path("src/github/source_service.py"))
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
async def test_github_scope_completeness_no_session_is_forced_false():
    module = _load_github_source_service_lightweight()
    issue = await module.prepare_github_issue_source("acme", "repo", 1, session_id=None, include_assets=False)
    ledger = issue["bundle"]["completeness_ledger"]
    assert issue["bundle"]["context_ref"] is None
    assert issue["bundle"]["digest_ref"] is None
    assert "session_scope_missing" in ledger["partial_reasons"]
    assert ledger["source_complete_for_generation"] is False
    assert ledger["source_complete_including_binary_bodies"] is False
    assert ledger["source_complete"] is False


@pytest.mark.asyncio
async def test_github_scope_completeness_with_session_can_remain_true():
    module = _load_github_source_service_lightweight()
    pr = await module.prepare_github_pr_source("acme", "repo", 2, session_id="s1", include_assets=False)
    ledger = pr["bundle"]["completeness_ledger"]
    assert pr["bundle"]["context_ref"] is not None
    assert pr["bundle"]["digest_ref"] is not None
    assert ledger["source_complete_for_generation"] is True
    assert ledger["source_complete_including_binary_bodies"] is True
    assert ledger["source_complete"] is True
