import base64
import types

import pytest

from tests._lightweight_source_service_loaders import load_github_source_service_lightweight


class _Ref:
    owner = "o"
    repo = "r"
    branch = "main"
    repo_full_name = "o/r"


@pytest.mark.asyncio
async def test_prepare_github_file_source_projectable_has_artifact_and_context():
    module, cleanup = load_github_source_service_lightweight()
    try:
        async def _fake_get_file(owner, repo, path, ref):
            return {"content": base64.b64encode(b"hello").decode(), "sha": "s", "size": 5}

        class _Parse:
            success = True
            markdown = "hello"
            blocks = [{"type": "paragraph"}]
            content_type = "text/plain"
            filename = "a.txt"

        async def _fake_save_uploaded_file(content, original_filename, session_id=None, content_type=None):
            return types.SimpleNamespace(file_id="f1", original_filename=original_filename, content_type=content_type, size=len(content), session_id=session_id)

        async def _fake_parse_file(file_id, options=None):
            return _Parse()

        module.github_channel = types.SimpleNamespace(get_file=_fake_get_file)
        module.save_uploaded_file = _fake_save_uploaded_file
        module.parse_file = _fake_parse_file

        prepared = await module.prepare_github_file_source("docs/a.txt", _Ref(), session_id="s1")
        assert prepared["bundle"]["artifact_refs"]
        assert prepared["bundle"]["context_ref"] == "ctx://gh"
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_prepare_github_file_source_non_projectable_marks_skipped():
    module, cleanup = load_github_source_service_lightweight()
    try:
        async def _fake_get_file(owner, repo, path, ref):
            return {"content": base64.b64encode(b"\x00\x01").decode(), "sha": "s", "size": 2}

        async def _fake_save_uploaded_file(content, original_filename, session_id=None, content_type=None):
            return types.SimpleNamespace(file_id="f2", original_filename=original_filename, content_type=content_type, size=len(content), session_id=session_id)

        module.github_channel = types.SimpleNamespace(get_file=_fake_get_file)
        module.save_uploaded_file = _fake_save_uploaded_file

        prepared = await module.prepare_github_file_source("docs/a.bin", _Ref(), session_id="s1")
        artifact_id = prepared["bundle"]["artifact_refs"][0]["artifact_id"]
        artifact = module._test_storage.get_artifact(artifact_id)
        assert artifact is not None
        assert artifact.parse_status == "skipped"
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_prepare_github_file_source_parse_failed_sets_failed_status():
    module, cleanup = load_github_source_service_lightweight()
    try:
        async def _fake_get_file(owner, repo, path, ref):
            return {"content": base64.b64encode(b"hello").decode(), "sha": "s", "size": 5}

        class _Parse:
            success = False
            error = "parse error"

        async def _fake_save_uploaded_file(content, original_filename, session_id=None, content_type=None):
            return types.SimpleNamespace(file_id="f3", original_filename=original_filename, content_type=content_type, size=len(content), session_id=session_id)

        async def _fake_parse_file(file_id, options=None):
            return _Parse()

        module.github_channel = types.SimpleNamespace(get_file=_fake_get_file)
        module.save_uploaded_file = _fake_save_uploaded_file
        module.parse_file = _fake_parse_file

        prepared = await module.prepare_github_file_source("docs/a.txt", _Ref(), session_id="s1")
        artifact_id = prepared["bundle"]["artifact_refs"][0]["artifact_id"]
        artifact = module._test_storage.get_artifact(artifact_id)
        assert artifact is not None
        assert artifact.parse_status == "failed"
    finally:
        cleanup()
