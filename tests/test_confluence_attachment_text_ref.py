import pytest


@pytest.mark.asyncio
async def test_confluence_attachment_builds_text_ref_and_artifact_ref(monkeypatch):
    import src.confluence as confluence

    class _Channel:
        base_url = "https://c"
        _auth_header = {}

        def is_configured(self):
            return True

        def get_instance_client(self, **kwargs):
            return self

        async def get_page(self, page_id):
            return {"id": page_id, "title": "T", "space": {"key": "ENG"}, "body": {"storage": {"value": "<p>x</p>"}}}

        async def get_all_comments_with_ledger(self, page_id):
            return [], {"loaded": 0, "total": 0, "complete": True}

        async def get_all_attachments_with_ledger(self, page_id):
            return [
                {"id": "d1", "title": "a.pdf", "metadata": {"mediaType": "application/pdf"}, "_links": {"download": "/d"}}
            ], {"loaded": 1, "total": 1, "complete": True}

        async def get_all_page_children_with_ledger(self, page_id):
            return [], {"loaded": 0, "total": 0, "complete": True}

        async def get_all_descendants_with_ledger(self, page_id):
            return [], {"loaded": 0, "total": 0, "complete": True, "partial_reasons": []}

    class _Adapter:
        def __init__(self, _):
            pass

        async def _to_markdown(self, page):
            return "x"

    class _Res:
        artifact_id = "a1"
        preview = "doc"
        content = "doc"
        text_ref = "txt-ref"

    monkeypatch.setattr(confluence, "confluence_channel", _Channel())
    monkeypatch.setattr(confluence, "ConfluenceFormatAdapter", _Adapter)

    async def _fake_download(**kwargs):
        return _Res()

    monkeypatch.setattr(confluence, "download_and_process_attachment", _fake_download)
    captured = {}

    def _fake_persist(**kwargs):
        captured["bundle"] = kwargs["bundle"]
        return {"context_ref": "c", "digest_ref": "d"}

    monkeypatch.setattr(confluence, "persist_confluence_source_bundle_and_digest", _fake_persist)

    from src.file_artifacts.storage import storage as artifact_storage
    from src.file_artifacts.models import ArtifactRecord

    def _fake_get_artifact(_artifact_id):
        return ArtifactRecord(
            artifact_id="a1", file_id="a1", source_type="confluence", source_kind="page_attachment", filename="a.pdf", content_type="application/pdf", size=1, text_ref="txt-ref"
        )

    monkeypatch.setattr(artifact_storage, "get_artifact", _fake_get_artifact)

    out = await confluence.confluence_prepare_page_context("1", _session_id="s")
    assert "[confluence source bundle prepared]" in out
    assert captured["bundle"]["attachments"][0]["text_ref"] == "txt-ref"
    assert captured["bundle"]["artifact_refs"][0]["text_ref"] == "txt-ref"
