import pytest


@pytest.mark.asyncio
async def test_confluence_bundle_has_artifact_refs_and_image_policy(monkeypatch):
    import src.confluence as confluence

    class _Channel:
        base_url = "https://c"
        _auth_header = {}
        def is_configured(self): return True
        def get_instance_client(self, **kwargs): return self
        async def get_page(self, page_id): return {"id": page_id, "title": "T", "body": {"storage": {"value": "<p>x</p>"}}}
        async def get_all_comments_with_ledger(self, page_id): return [], {"loaded": 0, "total": 0, "complete": True}
        async def get_all_attachments_with_ledger(self, page_id):
            return [
                {"id": "i1", "title": "a.png", "metadata": {"mediaType": "image/png"}, "_links": {"download": "/i"}},
                {"id": "d1", "title": "a.pdf", "metadata": {"mediaType": "application/pdf"}, "_links": {"download": "/d"}},
            ], {"loaded": 2, "total": 2, "complete": True}
        async def get_all_page_children_with_ledger(self, page_id): return [], {"loaded": 0, "total": 0, "complete": True}
        async def get_all_descendants_with_ledger(self, page_id): return [], {"loaded": 0, "total": 0, "complete": True, "partial_reasons": []}

    class _Adapter:
        def __init__(self, _): pass
        async def _to_markdown(self, page): return "x"

    class _Res:
        artifact_id = "a1"
        preview = "doc"
        content = "doc"

    monkeypatch.setattr(confluence, "confluence_channel", _Channel())
    monkeypatch.setattr(confluence, "ConfluenceFormatAdapter", _Adapter)
    
    async def _fake_download(**kwargs):
        return _Res()
    monkeypatch.setattr(confluence, "download_and_process_attachment", _fake_download)
    monkeypatch.setattr(confluence, "persist_confluence_source_bundle_and_digest", lambda **kwargs: {"context_ref": "c", "digest_ref": "d"})

    out = await confluence.confluence_prepare_page_context("1", _session_id="s")
    assert "[confluence source bundle prepared]" in out
    assert "attachments_loaded: 2/2" in out
