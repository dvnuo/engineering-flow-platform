import base64
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_prepare_github_file_source_session_scope_without_unknown_session(monkeypatch):
    try:
        from src.github.source_service import prepare_github_file_source
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"github source service import unavailable in this environment: {exc}")

    async def _fake_get_file(owner, repo, path, ref):
        return {"content": base64.b64encode(b"hello").decode(), "sha": "sha1", "size": 5}

    monkeypatch.setattr("src.github.source_service.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr(
        "src.github.source_service.register_existing_file_as_artifact",
        lambda file_id, **kwargs: SimpleNamespace(
            artifact_id=file_id,
            file_id=file_id,
            filename="a.txt",
            content_type="text/plain",
            source_type=kwargs.get("source_type"),
            source_kind=kwargs.get("source_kind"),
            source_locator=kwargs.get("source_locator"),
            projection_kind=None,
            preview=None,
            text_ref=None,
            context_ref=None,
            digest_ref=None,
        ),
    )

    captured = {}

    def _fake_persist(**kwargs):
        captured["session_id"] = kwargs["session_id"]
        return {"context_ref": "ctx://context/s1/github_source/abc123def456", "digest_ref": "ctx://context/s1/github_digest/abc123def456"}

    monkeypatch.setattr("src.github.source_service.persist_github_source_bundle_and_digest", _fake_persist)

    class _DefaultRef:
        owner = "acme"
        repo = "platform"
        path = "bundles/r1"
        branch = "main"

    with_session = await prepare_github_file_source("docs/a.txt", _DefaultRef(), session_id="s1")
    assert captured["session_id"] == "s1"
    assert with_session["bundle"]["context_ref"] is not None
    assert "unknown_session" not in with_session["bundle"]["context_ref"]

    no_session = await prepare_github_file_source("docs/a.txt", _DefaultRef(), session_id=None)
    assert no_session["bundle"]["context_ref"] is None
    assert no_session["bundle"]["digest_ref"] is None
    assert "session_scope_missing" in (no_session["bundle"].get("completeness_ledger") or {}).get("partial_reasons", [])


def test_prepare_github_doc_source_signature_and_passthrough_regression():
    source = Path("src/runtime/requirement_bundle_assets.py").read_text(encoding="utf-8")
    assert "async def prepare_github_doc_source(" in source
    assert "session_id: str | None = None" in source
    assert "prepare_github_file_source(raw, default_ref, session_id=session_id)" in source
    assert "async def read_github_doc_text(" in source
