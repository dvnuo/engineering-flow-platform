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
