import importlib.util
import sys
import types
from pathlib import Path


def _load_github_modules_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    github_pkg = types.ModuleType("src.github")
    github_pkg.__path__ = []

    file_artifacts_pkg = types.ModuleType("src.file_artifacts")
    file_artifacts_pkg.__path__ = []
    file_artifacts_pkg.can_project_to_text = lambda content_type, filename: str(content_type or "").startswith("text/") or str(content_type or "") in {"application/json", "application/pdf"}

    service_mod = types.ModuleType("src.file_artifacts.service")
    service_mod.attach_source_refs_to_artifact = lambda *args, **kwargs: None
    service_mod.bind_artifact_to_source_bundle = lambda *args, **kwargs: None
    service_mod.build_artifact_ref_dict = lambda record: {"artifact_id": record.artifact_id, "context_ref": getattr(record, "context_ref", None), "digest_ref": getattr(record, "digest_ref", None), "text_ref": getattr(record, "text_ref", None)}
    service_mod.register_existing_file_as_artifact = lambda *args, **kwargs: None
    service_mod.update_projection_from_parse_result = lambda *args, **kwargs: None

    storage_mod = types.ModuleType("src.file_artifacts.storage")

    class _Storage:
        def get_artifact(self, _artifact_id):
            return None

        def update_artifact_status(self, *args, **kwargs):
            return None

    storage_mod.storage = _Storage()

    github_api_mod = types.ModuleType("src.github.api")
    github_api_mod.github_channel = types.SimpleNamespace()

    github_links_mod = types.ModuleType("src.github.asset_links")
    github_links_mod.extract_github_asset_urls = lambda text: []

    github_doc_ref_mod = types.ModuleType("src.github.doc_refs")
    github_doc_ref_mod.parse_github_doc_ref = lambda raw, default_ref: default_ref

    source_context_mod = types.ModuleType("src.source_context")
    source_context_mod.persist_github_source_bundle_and_digest = lambda **kwargs: {"context_ref": "ctx", "digest_ref": "dig"}
    scope_mod = types.ModuleType("src.source_bundle_completeness")
    spec_scope = importlib.util.spec_from_file_location("src.source_bundle_completeness", Path("src/source_bundle_completeness.py"))
    assert spec_scope and spec_scope.loader
    spec_scope.loader.exec_module(scope_mod)

    utils_pkg = types.ModuleType("src.utils")
    utils_pkg.__path__ = []

    attachment_mod = types.ModuleType("src.utils.attachment")
    attachment_mod.download_and_process_attachment = None

    file_parser_mod = types.ModuleType("src.utils.file_parser")
    file_parser_mod.parse_file = None
    file_parser_mod.save_uploaded_file = None

    modules = {
        "src": src_pkg,
        "src.github": github_pkg,
        "src.file_artifacts": file_artifacts_pkg,
        "src.file_artifacts.service": service_mod,
        "src.file_artifacts.storage": storage_mod,
        "src.github.api": github_api_mod,
        "src.github.asset_links": github_links_mod,
        "src.github.doc_refs": github_doc_ref_mod,
        "src.source_context": source_context_mod,
        "src.source_bundle_completeness": scope_mod,
        "src.utils": utils_pkg,
        "src.utils.attachment": attachment_mod,
        "src.utils.file_parser": file_parser_mod,
    }

    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        service_spec = importlib.util.spec_from_file_location("src.github.source_service", Path("src/github/source_service.py"))
        service_module = importlib.util.module_from_spec(service_spec)
        assert service_spec and service_spec.loader
        service_spec.loader.exec_module(service_module)

        manifest_spec = importlib.util.spec_from_file_location("src.github.source_manifest", Path("src/github/source_manifest.py"))
        manifest_module = importlib.util.module_from_spec(manifest_spec)
        assert manifest_spec and manifest_spec.loader
        manifest_spec.loader.exec_module(manifest_module)
        return service_module, manifest_module
    finally:
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def test_build_asset_ledger_and_manifest_contracts():
    source_service, source_manifest = _load_github_modules_lightweight()

    empty = source_service._build_asset_ledger(
        source_kind="issue",
        body_loaded=True,
        body_nonempty=False,
        comments_loaded=True,
        review_comments_loaded=True,
        asset_entries=[],
        partial_reasons=[],
    )
    assert empty["source_complete_for_generation"] is True

    all_text = source_service._build_asset_ledger(
        source_kind="pull_request",
        body_loaded=True,
        body_nonempty=True,
        comments_loaded=True,
        review_comments_loaded=True,
        asset_entries=[{"content_type": "text/plain", "filename": "a.txt", "parse_status": "completed", "projected_to_text": True, "text_ref": "ctx://t1"}],
        partial_reasons=[],
    )
    assert all_text["source_complete_for_generation"] is True
    assert all_text["text_assets_loaded"] == all_text["projectable_assets_total"]

    has_binary = source_service._build_asset_ledger(
        source_kind="issue",
        body_loaded=True,
        body_nonempty=True,
        comments_loaded=True,
        review_comments_loaded=True,
        asset_entries=[
            {"content_type": "text/plain", "filename": "a.txt", "parse_status": "completed", "projected_to_text": True, "text_ref": "ctx://t1"},
            {"content_type": "application/octet-stream", "filename": "blob.bin", "parse_status": "completed", "projected_to_text": False, "text_ref": None},
        ],
        partial_reasons=[],
    )
    assert has_binary["source_complete_for_generation"] is True
    assert has_binary["source_complete_including_binary_bodies"] is False

    manifest_repo = source_manifest.format_github_source_manifest(
        {
            "metadata": {"source_kind": "repo_file", "owner": "acme", "repo": "platform", "path": "docs/a.md", "branch": "main"},
            "artifact_refs": [{"artifact_id": "a1"}],
            "context_ref": "ctx://1",
            "digest_ref": "dig://1",
            "content_markdown": "hello",
            "completeness_ledger": {"source_complete": True},
        }
    )
    assert "artifact_refs:" in manifest_repo
    assert "context_ref: ctx://1" in manifest_repo
    assert "digest_ref: dig://1" in manifest_repo

    manifest_issue = source_manifest.format_github_source_manifest(
        {
            "metadata": {"source_kind": "issue", "repo_full_name": "acme/platform", "issue_number": 1},
            "artifact_refs": [{"artifact_id": "a1"}],
            "context_ref": "ctx://1",
            "digest_ref": "dig://1",
            "body_markdown": "",
            "completeness_ledger": has_binary,
        }
    )
    assert "source_complete_for_generation:" in manifest_issue
    assert "source_complete_including_binary_bodies:" in manifest_issue
    assert "text_assets_loaded:" in manifest_issue
    assert "text_assets_with_full_ref:" in manifest_issue
    assert "non_projectable_assets_total:" in manifest_issue


def test_finalize_bundle_artifacts_refreshes_entry_and_bundle_refs():
    source_service, _ = _load_github_modules_lightweight()

    bind_calls = []
    attach_calls = []
    records = {
        "a1": types.SimpleNamespace(artifact_id="a1", text_ref="ctx://t/a1", context_ref="ctx://bundle", digest_ref="ctx://digest"),
        "a2": types.SimpleNamespace(artifact_id="a2", text_ref="ctx://t/a2", context_ref="ctx://bundle", digest_ref="ctx://digest"),
    }

    source_service.bind_artifact_to_source_bundle = lambda artifact_id, scope_id: bind_calls.append((artifact_id, scope_id))
    source_service.attach_source_refs_to_artifact = lambda artifact_id, **kwargs: attach_calls.append((artifact_id, kwargs.get("context_ref"), kwargs.get("digest_ref")))
    source_service.artifact_storage = types.SimpleNamespace(get_artifact=lambda artifact_id: records.get(artifact_id))
    source_service.build_artifact_ref_dict = lambda record: {"artifact_id": record.artifact_id, "text_ref": record.text_ref, "context_ref": record.context_ref, "digest_ref": record.digest_ref}

    entries, refs = source_service._finalize_bundle_artifacts(
        asset_entries=[
            {"artifact_id": "a1", "text_ref": None},
            {"artifact_id": "a1", "text_ref": None},
            {"artifact_id": "a2", "text_ref": ""},
        ],
        bundle_scope_id="github:acme/platform#issue:1",
        context_ref="ctx://bundle",
        digest_ref="ctx://digest",
    )

    assert len(bind_calls) == 3
    assert len(attach_calls) == 3
    assert all(e["artifact_ref"]["context_ref"] == "ctx://bundle" for e in entries)
    assert all(e.get("text_ref") for e in entries)
    assert [r["artifact_id"] for r in refs] == ["a1", "a2"]
