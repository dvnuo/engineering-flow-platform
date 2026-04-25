import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_confluence_source_service_with_stubs():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    confluence_pkg = types.ModuleType("src.confluence")
    confluence_pkg.__path__ = []

    file_artifacts_pkg = types.ModuleType("src.file_artifacts")
    file_artifacts_pkg.__path__ = []
    file_artifacts_pkg.can_project_to_text = lambda mime, filename: str(mime or "").startswith("text/") or str(mime or "") in {"application/pdf", "application/json"}

    fa_service = types.ModuleType("src.file_artifacts.service")
    fa_service.attach_source_refs_to_artifact = lambda *args, **kwargs: None
    fa_service.bind_artifact_to_source_bundle = lambda *args, **kwargs: None
    fa_service.build_artifact_ref_dict = lambda record: {"artifact_id": getattr(record, "artifact_id", "a1")}

    fa_storage = types.ModuleType("src.file_artifacts.storage")
    fa_storage.storage = types.SimpleNamespace(get_artifact=lambda artifact_id: None)

    source_context = types.ModuleType("src.source_context")
    source_context.persist_confluence_source_bundle_and_digest = lambda **kwargs: {"context_ref": "ctx://conf", "digest_ref": "ctx://conf/d"}

    scope_mod = types.ModuleType("src.source_bundle_completeness")
    spec_scope = importlib.util.spec_from_file_location("src.source_bundle_completeness", Path("src/source_bundle_completeness.py"))
    assert spec_scope and spec_scope.loader
    spec_scope.loader.exec_module(scope_mod)

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
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location("src.confluence.source_service", Path("src/confluence/source_service.py"))
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


class _Channel:
    base_url = "https://c"
    _auth_header = {}

    def __init__(self, attachments):
        self._attachments = attachments

    def is_configured(self):
        return True

    def get_instance_client(self, **kwargs):
        return self

    async def get_page(self, page_id):
        return {"id": page_id, "title": "T", "space": {"key": "ENG"}, "body": {"storage": {"value": "<p>x</p>"}}}

    async def get_all_comments_with_ledger(self, page_id):
        return [], {"loaded": 0, "total": 0, "complete": True}

    async def get_all_attachments_with_ledger(self, page_id):
        return self._attachments, {"loaded": len(self._attachments), "total": len(self._attachments), "complete": True}

    async def get_all_page_children_with_ledger(self, page_id):
        return [], {"loaded": 0, "total": 0, "complete": True}

    async def get_all_descendants_with_ledger(self, page_id):
        return [], {"loaded": 0, "total": 0, "complete": True, "partial_reasons": []}


@pytest.mark.asyncio
async def test_confluence_image_only_attachments_keep_generation_complete_but_binary_incomplete():
    module = _load_confluence_source_service_with_stubs()
    channel = _Channel([
        {"id": "i1", "title": "a.png", "metadata": {"mediaType": "image/png"}, "_links": {"download": "/i1"}},
    ])

    out = await module.prepare_confluence_page_source("1", session_id="s1", channel=channel)
    ledger = out["bundle"]["completeness_ledger"]

    assert ledger["source_complete_for_generation"] is True
    assert ledger["source_complete_including_binary_bodies"] is False
    assert ledger["non_projectable_attachments_total"] == 1
    assert ledger["binary_attachment_bodies_skipped_count"] == 1
    assert ledger["binary_attachment_body_policy"] == "metadata_only"


@pytest.mark.asyncio
async def test_confluence_text_attachments_can_keep_generation_and_binary_complete():
    module = _load_confluence_source_service_with_stubs()
    channel = _Channel([
        {"id": "d1", "title": "a.pdf", "metadata": {"mediaType": "application/pdf"}, "_links": {"download": "/d1"}},
    ])

    class _Result:
        artifact_id = None
        preview = "doc"
        content = "doc"
        text_ref = "ctx://text/1"
        parse_status = "completed"
        parse_error = None
        projected_to_text = True

    async def _fake_download(**kwargs):
        return _Result()

    out = await module.prepare_confluence_page_source("1", session_id="s1", channel=channel, downloader=_fake_download)
    ledger = out["bundle"]["completeness_ledger"]

    assert ledger["source_complete_for_generation"] is True
    assert ledger["source_complete_including_binary_bodies"] is True
    assert ledger["non_projectable_attachments_total"] == 0
    assert ledger["binary_attachment_bodies_skipped_count"] == 0
    assert ledger["binary_attachment_body_policy"] == "loaded"
