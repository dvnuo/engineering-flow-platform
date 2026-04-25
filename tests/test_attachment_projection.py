import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_attachment_module_with_stubs():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    file_artifacts_pkg = types.ModuleType("src.file_artifacts")
    file_artifacts_pkg.__path__ = []
    file_artifacts_pkg.can_project_to_text = lambda content_type, filename: str(content_type or "").startswith("text/") or str(content_type or "") in {"application/pdf", "application/json"}

    state = {}

    class _Artifact:
        def __init__(self, artifact_id):
            self.artifact_id = artifact_id
            self.text_ref = state.get(artifact_id, {}).get("text_ref")
            self.projection_kind = state.get(artifact_id, {}).get("projection_kind")
            self.preview = state.get(artifact_id, {}).get("preview")
            self.parse_status = state.get(artifact_id, {}).get("parse_status", "pending")
            self.parse_error = state.get(artifact_id, {}).get("parse_error")

    def _register_existing_file_as_artifact(file_id, **kwargs):
        state.setdefault(file_id, {})
        return _Artifact(file_id)

    def _update_projection_from_parse_result(artifact_id, parsed, **kwargs):
        state.setdefault(artifact_id, {})
        state[artifact_id]["projection_kind"] = "markdown"
        state[artifact_id]["preview"] = (getattr(parsed, "markdown", "") or "")[:2000]
        state[artifact_id]["parse_status"] = "completed"
        state[artifact_id]["parse_error"] = None
        if kwargs.get("persist_text_ref_session_id") and kwargs.get("persist_text_ref_kind") and kwargs.get("persist_text_ref_source_id") and kwargs.get("persist_text_ref_title"):
            state[artifact_id]["text_ref"] = f"ctx://text/{artifact_id}"
        return _Artifact(artifact_id)

    fa_service = types.ModuleType("src.file_artifacts.service")
    fa_service.register_existing_file_as_artifact = _register_existing_file_as_artifact
    fa_service.update_projection_from_parse_result = _update_projection_from_parse_result

    storage_mod = types.ModuleType("src.file_artifacts.storage")

    class _Storage:
        def update_artifact_status(self, artifact_id, *, parse_status, parse_error=None):
            state.setdefault(artifact_id, {})
            state[artifact_id]["parse_status"] = parse_status
            state[artifact_id]["parse_error"] = parse_error
            return _Artifact(artifact_id)

        def get_artifact(self, artifact_id):
            if artifact_id not in state:
                return None
            return _Artifact(artifact_id)

    storage_mod.storage = _Storage()

    utils_pkg = types.ModuleType("src.utils")
    utils_pkg.__path__ = []

    file_parser_mod = types.ModuleType("src.utils.file_parser")

    async def _save_uploaded_file(content, original_filename, session_id=None, content_type=None):
        file_id = f"file-{len(state) + 1}"
        return types.SimpleNamespace(file_id=file_id, size=len(content or b""), uploaded_at="now")

    async def _parse_file(*args, **kwargs):
        raise NotImplementedError

    file_parser_mod.save_uploaded_file = _save_uploaded_file
    file_parser_mod.parse_file = _parse_file
    file_parser_mod.get_file_path = lambda file_id: Path(f"/tmp/{file_id}")
    file_parser_mod.compress_image_for_llm = lambda path, max_dimension=1024: "base64"

    modules = {
        "src": src_pkg,
        "src.file_artifacts": file_artifacts_pkg,
        "src.file_artifacts.service": fa_service,
        "src.file_artifacts.storage": storage_mod,
        "src.utils": utils_pkg,
        "src.utils.file_parser": file_parser_mod,
    }

    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location("src.utils.attachment", Path("src/utils/attachment.py"))
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module, state, storage_mod.storage
    finally:
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


@pytest.mark.asyncio
async def test_attachment_projectable_success_and_persist_text_ref():
    attachment_mod, _state, _storage = _load_attachment_module_with_stubs()

    async def _fake_download(url, auth_header=None):
        return (b"%PDF-1.4 fake", "application/pdf", "a.pdf")

    async def _fake_parse(file_id, options=None):
        return types.SimpleNamespace(success=True, markdown="parsed pdf", blocks=[], content_type="application/pdf", filename="a.pdf")

    attachment_mod._download_file = _fake_download
    attachment_mod.parse_file = _fake_parse

    out = await attachment_mod.download_and_process_attachment(
        "u",
        source_type="jira",
        source_kind="issue_attachment",
        persist_text_ref_session_id="s1",
        persist_text_ref_kind="jira_attachment_text",
        persist_text_ref_source_id="P-1:1",
        persist_text_ref_title="Jira attachment text",
    )
    assert out.content_format == "text"
    assert out.artifact_id
    assert out.parse_status == "completed"
    assert out.projection_kind == "markdown"
    assert out.text_ref and out.text_ref.startswith("ctx://text/")


@pytest.mark.asyncio
async def test_attachment_non_projectable_binary_is_metadata_skipped():
    attachment_mod, _state, storage = _load_attachment_module_with_stubs()

    async def _fake_download(url, auth_header=None):
        return (b"\x00\x01\x02", "application/octet-stream", "a.bin")

    attachment_mod._download_file = _fake_download

    out = await attachment_mod.download_and_process_attachment("u")
    assert out.content_format == "metadata"
    assert out.parse_status == "skipped"
    assert storage.get_artifact(out.artifact_id).parse_status == "skipped"


@pytest.mark.asyncio
async def test_attachment_parse_failed_and_exception_paths_mark_failed():
    attachment_mod, _state, storage = _load_attachment_module_with_stubs()

    async def _fake_download(url, auth_header=None):
        return (b"hello", "text/plain", "a.txt")

    attachment_mod._download_file = _fake_download

    async def _parse_failed(file_id, options=None):
        return types.SimpleNamespace(success=False, error="parse failed")

    attachment_mod.parse_file = _parse_failed
    out_failed = await attachment_mod.download_and_process_attachment("u")
    assert out_failed.content_format == "metadata"
    assert out_failed.parse_status == "failed"
    assert storage.get_artifact(out_failed.artifact_id).parse_status == "failed"

    async def _parse_exception(file_id, options=None):
        raise RuntimeError("boom")

    attachment_mod.parse_file = _parse_exception
    out_exc = await attachment_mod.download_and_process_attachment("u")
    assert out_exc.content_format == "metadata"
    assert out_exc.parse_status == "failed"
    assert storage.get_artifact(out_exc.artifact_id).parse_status == "failed"
