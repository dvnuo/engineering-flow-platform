import base64

import pytest

from src.runtime.requirement_bundle_assets import BundleRef, RequirementBundleError, read_github_doc_text


@pytest.mark.asyncio
async def test_prepare_github_file_source_and_read_text(monkeypatch):
    from src.github.source_service import prepare_github_file_source

    async def _fake_get_file(owner, repo, path, ref):
        return {"content": base64.b64encode(b"hello").decode(), "sha": "s", "size": 5}

    monkeypatch.setattr("src.github.source_service.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)

    ref = BundleRef(owner="o", repo="r", path="p", branch="main")
    prepared = await prepare_github_file_source("docs/a.txt", ref, session_id="s1")
    assert prepared["bundle"]["artifact_refs"]
    assert prepared["bundle"]["context_ref"]
    doc_ref, text = await read_github_doc_text("docs/a.txt", ref)
    assert doc_ref.path == "docs/a.txt"
    assert "hello" in text


@pytest.mark.asyncio
async def test_read_github_doc_text_non_projectable(monkeypatch):
    async def _fake_get_file(owner, repo, path, ref):
        return {"content": base64.b64encode(b"\x00\x01").decode(), "sha": "s", "size": 2}

    monkeypatch.setattr("src.github.source_service.github_channel.get_file", _fake_get_file)

    ref = BundleRef(owner="o", repo="r", path="p", branch="main")
    with pytest.raises(RequirementBundleError):
        await read_github_doc_text("docs/a.bin", ref)
