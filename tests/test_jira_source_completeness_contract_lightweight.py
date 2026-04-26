import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_jira_source_service_with_stubs():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    jira_pkg = types.ModuleType("src.jira")
    jira_pkg.__path__ = []

    file_artifacts_pkg = types.ModuleType("src.file_artifacts")
    file_artifacts_pkg.__path__ = []
    file_artifacts_pkg.can_project_to_text = lambda mime, filename: str(mime or "").startswith("text/") or str(mime or "") in {"application/pdf", "application/json"}

    fa_service = types.ModuleType("src.file_artifacts.service")
    fa_service.attach_source_refs_to_artifact = lambda *args, **kwargs: None
    fa_service.bind_artifact_to_source_bundle = lambda *args, **kwargs: None
    fa_service.build_artifact_ref_dict = lambda record, text_ref=None: {"artifact_id": getattr(record, "artifact_id", "a1"), "text_ref": text_ref or getattr(record, "text_ref", None)}

    fa_storage = types.ModuleType("src.file_artifacts.storage")
    fa_storage.storage = types.SimpleNamespace(get_artifact=lambda artifact_id: None)

    source_context = types.ModuleType("src.source_context")
    source_context.persist_jira_source_bundle_and_digest = lambda **kwargs: {"context_ref": "ctx://jira", "digest_ref": "ctx://jira/d", "source_digest_chunk_count": 0}
    scope_mod = types.ModuleType("src.source_bundle_completeness")

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

    scope_mod.apply_session_scope_requirement = apply_session_scope_requirement

    utils_pkg = types.ModuleType("src.utils")
    utils_pkg.__path__ = []
    attachment_mod = types.ModuleType("src.utils.attachment")
    attachment_mod.download_and_process_attachment = None

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
                    "attachment": [{"id": "1", "filename": "a.pdf", "mimeType": "application/pdf", "content": "u"}],
                },
                "names": {},
            }

        def _get_comments_list(self, *_args, **_kwargs):
            return []

        def _convert_description_to_markdown(self, _value):
            return ""

        def _extract_acceptance_criteria(self, _issue):
            return ""

    adapter_mod.JiraFormatAdapter = JiraFormatAdapter

    jira_module = types.ModuleType("src.jira")

    class _Channel:
        api_version = "3"
        _auth_header = {}

        def is_configured(self):
            return True

        def get_instance_client(self, **kwargs):
            return self

    jira_module.jira_channel = _Channel()

    class _Failed:
        artifact_id = None
        text_ref = None
        content = "[application/pdf: a.pdf]"
        parse_status = "failed"
        parse_error = "parse failed"
        projected_to_text = False

    async def _fake_download(**kwargs):
        return _Failed()

    jira_module.download_and_process_attachment = _fake_download

    src_pkg.jira = jira_module

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
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    sys.modules["src"].jira = jira_module
    sys.modules["src.jira"] = jira_module
    spec = importlib.util.spec_from_file_location("src.jira.source_service", Path("src/jira/source_service.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["src.jira.source_service"] = module
    spec.loader.exec_module(module)

    def _cleanup():
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        sys.modules.pop("src.jira.source_service", None)

    return module, _cleanup


@pytest.mark.asyncio
async def test_jira_parse_failure_and_session_scope_lightweight():
    module, cleanup = _load_jira_source_service_with_stubs()
    try:
        no_session = await module.prepare_jira_issue_source("P-1", session_id=None)
        no_session_ledger = no_session.bundle["completeness_ledger"]
        assert no_session.manifest["context_ref"] is None
        assert "session_scope_missing" in no_session_ledger["partial_reasons"]

        with_session = await module.prepare_jira_issue_source("P-1", session_id="s1")
        ledger = with_session.bundle["completeness_ledger"]
        assert with_session.manifest["context_ref"] == "ctx://jira"
        assert ledger["source_complete_for_generation"] is False
        assert ledger["source_complete"] is False
    finally:
        cleanup()
