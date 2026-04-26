from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


class _Storage:
    def __init__(self):
        self.records: dict[str, object] = {}

    def get_artifact(self, artifact_id: str):
        return self.records.get(str(artifact_id))

    def upsert_artifact(self, record):
        self.records[str(record.artifact_id)] = record
        return record

    def update_artifact_status(self, artifact_id, *, parse_status=None, parse_error=None):
        rec = self.get_artifact(artifact_id)
        if rec is None:
            return None
        if parse_status is not None:
            setattr(rec, "parse_status", parse_status)
        if parse_error is not None:
            setattr(rec, "parse_error", parse_error)
        return rec

    def update_artifact_projection(self, artifact_id, *, projection_kind=None, preview=None, chunk_count=None, total_chars=None):
        rec = self.get_artifact(artifact_id)
        if rec is None:
            return None
        if projection_kind is not None:
            setattr(rec, "projection_kind", projection_kind)
        if preview is not None:
            setattr(rec, "preview", preview)
        if chunk_count is not None:
            setattr(rec, "chunk_count", chunk_count)
        if total_chars is not None:
            setattr(rec, "total_chars", total_chars)
        return rec

    def update_artifact_references(self, artifact_id, *, text_ref=None, context_ref=None, digest_ref=None, full_markdown_chars=None):
        rec = self.get_artifact(artifact_id)
        if rec is None:
            return None
        if text_ref is not None:
            setattr(rec, "text_ref", text_ref)
        if context_ref is not None:
            setattr(rec, "context_ref", context_ref)
        if digest_ref is not None:
            setattr(rec, "digest_ref", digest_ref)
        if full_markdown_chars is not None:
            setattr(rec, "full_markdown_chars", full_markdown_chars)
        return rec


def _scope_stub_module() -> types.ModuleType:
    module = types.ModuleType("src.source_bundle_completeness")

    def apply_session_scope_requirement(ledger, *, has_context_ref, has_digest_ref):
        if has_context_ref and has_digest_ref:
            return ledger
        partial_reasons = ledger.setdefault("partial_reasons", [])
        if "session_scope_missing" not in partial_reasons:
            partial_reasons.append("session_scope_missing")
        ledger["source_complete_for_generation"] = False
        ledger["source_complete_including_binary_bodies"] = False
        ledger["source_complete"] = False
        return ledger

    module.apply_session_scope_requirement = apply_session_scope_requirement
    return module


def _base_artifact_modules():
    file_artifacts_pkg = types.ModuleType("src.file_artifacts")
    file_artifacts_pkg.__path__ = []
    file_artifacts_pkg.can_project_to_text = (
        lambda mime, filename: str(mime or "").startswith("text/")
        or str(mime or "") in {"application/pdf", "application/json"}
    )

    storage_mod = types.ModuleType("src.file_artifacts.storage")
    storage = _Storage()
    storage_mod.storage = storage

    service_mod = types.ModuleType("src.file_artifacts.service")

    def attach_source_refs_to_artifact(artifact_id, *, context_ref=None, digest_ref=None):
        rec = storage.get_artifact(artifact_id)
        if rec is not None:
            setattr(rec, "context_ref", context_ref)
            setattr(rec, "digest_ref", digest_ref)

    service_mod.attach_source_refs_to_artifact = attach_source_refs_to_artifact
    service_mod.bind_artifact_to_source_bundle = lambda *args, **kwargs: None
    service_mod.build_artifact_ref_dict = lambda record, text_ref=None: {
        "artifact_id": getattr(record, "artifact_id", None),
        "text_ref": text_ref if text_ref is not None else getattr(record, "text_ref", None),
        "context_ref": getattr(record, "context_ref", None),
        "digest_ref": getattr(record, "digest_ref", None),
    }
    service_mod.register_existing_file_as_artifact = lambda file_id, **kwargs: storage.upsert_artifact(
        types.SimpleNamespace(
            artifact_id=str(file_id),
            file_id=str(file_id),
            filename=kwargs.get("provider_metadata", {}).get("path", kwargs.get("source_locator", str(file_id))),
            content_type=kwargs.get("provider_metadata", {}).get("content_type", "application/octet-stream"),
            source_type=kwargs.get("source_type"),
            source_kind=kwargs.get("source_kind"),
            source_locator=kwargs.get("source_locator"),
            projection_kind=None,
            preview=None,
            text_ref=None,
            context_ref=None,
            digest_ref=None,
            parse_status="pending",
            parse_error=None,
            chunk_count=0,
            total_chars=0,
            full_markdown_chars=0,
        )
    )

    def _update_projection_from_parse_result(artifact_id, parse_result, preview=None, **kwargs):
        markdown = getattr(parse_result, "markdown", "") or ""
        storage.update_artifact_projection(
            artifact_id,
            projection_kind="text",
            preview=preview if preview is not None else markdown[:2000],
            chunk_count=len(getattr(parse_result, "blocks", []) or []),
            total_chars=len(markdown),
        )
        storage.update_artifact_references(
            artifact_id,
            text_ref=f"ctx://context/{kwargs.get('persist_text_ref_session_id')}/{kwargs.get('persist_text_ref_kind')}/sha"
            if kwargs.get("persist_text_ref_session_id") and kwargs.get("persist_text_ref_kind")
            else None,
            full_markdown_chars=len(markdown),
        )
        return storage.update_artifact_status(artifact_id, parse_status="completed")

    service_mod.update_projection_from_parse_result = _update_projection_from_parse_result

    return file_artifacts_pkg, service_mod, storage_mod, storage


def _load_module_with_stubs(module_name: str, module_path: Path, modules: dict[str, types.ModuleType]):
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    def _cleanup():
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        sys.modules.pop(module_name, None)

    return module, _cleanup


def load_confluence_source_service_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    confluence_pkg = types.ModuleType("src.confluence")
    confluence_pkg.__path__ = []

    file_artifacts_pkg, fa_service, fa_storage, storage = _base_artifact_modules()

    source_context = types.ModuleType("src.source_context")
    source_context.persist_confluence_source_bundle_and_digest = (
        lambda **kwargs: {"context_ref": "ctx://conf", "digest_ref": "ctx://conf/d"}
    )

    scope_mod = _scope_stub_module()

    utils_pkg = types.ModuleType("src.utils")
    utils_pkg.__path__ = []
    attachment_mod = types.ModuleType("src.utils.attachment")
    attachment_mod.download_and_process_attachment = None

    adapter_mod = types.ModuleType("src.confluence.adapter")

    class ConfluenceFormatAdapter:
        def __init__(self, _channel):
            pass

        async def _to_markdown(self, _page):
            return "body"

    adapter_mod.ConfluenceFormatAdapter = ConfluenceFormatAdapter
    adapter_mod._extract_page_id_from_url = lambda _url: "1"

    api_mod = types.ModuleType("src.confluence.api")
    api_mod.ConfluenceChannel = object
    api_mod.confluence_channel = types.SimpleNamespace(is_configured=lambda: True)

    src_pkg.confluence = confluence_pkg
    confluence_pkg.api = api_mod
    confluence_pkg.adapter = adapter_mod

    modules = {
        "src": src_pkg,
        "src.confluence": confluence_pkg,
        "src.file_artifacts": file_artifacts_pkg,
        "src.file_artifacts.service": fa_service,
        "src.file_artifacts.storage": fa_storage,
        "src.source_context": source_context,
        "src.source_bundle_completeness": scope_mod,
        "src.utils": utils_pkg,
        "src.utils.attachment": attachment_mod,
        "src.confluence.adapter": adapter_mod,
        "src.confluence.api": api_mod,
    }

    module, cleanup = _load_module_with_stubs(
        "src.confluence.source_service", Path("src/confluence/source_service.py"), modules
    )
    module._test_storage = storage
    return module, cleanup


def load_jira_source_service_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    jira_pkg = types.ModuleType("src.jira")
    jira_pkg.__path__ = []

    file_artifacts_pkg, fa_service, fa_storage, storage = _base_artifact_modules()

    source_context = types.ModuleType("src.source_context")
    source_context.persist_jira_source_bundle_and_digest = (
        lambda **kwargs: {"context_ref": "ctx://jira", "digest_ref": "ctx://jira/d", "source_digest_chunk_count": 0}
    )

    scope_mod = _scope_stub_module()

    utils_pkg = types.ModuleType("src.utils")
    utils_pkg.__path__ = []
    attachment_mod = types.ModuleType("src.utils.attachment")

    async def _fake_download(**kwargs):
        return types.SimpleNamespace(
            artifact_id=None,
            text_ref=None,
            content="",
            parse_status="completed",
            parse_error=None,
            projected_to_text=True,
        )

    attachment_mod.download_and_process_attachment = _fake_download

    adapter_mod = types.ModuleType("src.jira.adapter")

    class JiraFormatAdapter:
        def __init__(self, channel):
            self.channel = channel

        async def get_issue(self, **kwargs):
            return {
                "key": "P-1",
                "fields": {
                    "summary": "S",
                    "comment": {"comments": [], "total": 0},
                    "attachment": [],
                },
                "names": {},
                "renderedFields": {},
            }

        def _get_comments_list(self, *_args, **_kwargs):
            return []

        def _convert_description_to_markdown(self, _value):
            return ""

        def _extract_acceptance_criteria(self, _issue):
            return ""

    adapter_mod.JiraFormatAdapter = JiraFormatAdapter

    class _Channel:
        api_version = "3"
        _auth_header = {}

        def is_configured(self):
            return True

        def get_instance_client(self, **kwargs):
            return self

    jira_pkg.jira_channel = _Channel()
    jira_pkg.download_and_process_attachment = _fake_download

    src_pkg.jira = jira_pkg

    modules = {
        "src": src_pkg,
        "src.jira": jira_pkg,
        "src.file_artifacts": file_artifacts_pkg,
        "src.file_artifacts.service": fa_service,
        "src.file_artifacts.storage": fa_storage,
        "src.source_context": source_context,
        "src.source_bundle_completeness": scope_mod,
        "src.utils": utils_pkg,
        "src.utils.attachment": attachment_mod,
        "src.jira.adapter": adapter_mod,
    }

    module, cleanup = _load_module_with_stubs(
        "src.jira.source_service", Path("src/jira/source_service.py"), modules
    )
    module._test_storage = storage
    return module, cleanup


def load_github_source_service_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    github_pkg = types.ModuleType("src.github")
    github_pkg.__path__ = []

    file_artifacts_pkg, fa_service, fa_storage, storage = _base_artifact_modules()

    github_api_mod = types.ModuleType("src.github.api")
    github_api_mod.github_channel = types.SimpleNamespace()

    github_links_mod = types.ModuleType("src.github.asset_links")
    github_links_mod.extract_github_asset_urls = lambda text: []

    github_doc_ref_mod = types.ModuleType("src.github.doc_refs")

    def _parse_github_doc_ref(raw, default_ref):
        return types.SimpleNamespace(
            owner=getattr(default_ref, "owner", "o"),
            repo=getattr(default_ref, "repo", "r"),
            branch=getattr(default_ref, "branch", "main"),
            path=str(raw),
        )

    github_doc_ref_mod.parse_github_doc_ref = _parse_github_doc_ref

    source_context_mod = types.ModuleType("src.source_context")
    source_context_mod.persist_github_source_bundle_and_digest = lambda **kwargs: {"context_ref": "ctx://gh", "digest_ref": "ctx://gh/d"}

    scope_mod = _scope_stub_module()

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
        "src.file_artifacts.service": fa_service,
        "src.file_artifacts.storage": fa_storage,
        "src.github.api": github_api_mod,
        "src.github.asset_links": github_links_mod,
        "src.github.doc_refs": github_doc_ref_mod,
        "src.source_context": source_context_mod,
        "src.source_bundle_completeness": scope_mod,
        "src.utils": utils_pkg,
        "src.utils.attachment": attachment_mod,
        "src.utils.file_parser": file_parser_mod,
    }
    module, cleanup = _load_module_with_stubs("src.github.source_service", Path("src/github/source_service.py"), modules)
    module._test_storage = storage
    return module, cleanup
