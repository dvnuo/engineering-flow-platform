import pytest

from tests._lightweight_source_service_loaders import load_confluence_source_service_lightweight


@pytest.mark.asyncio
async def test_prepare_confluence_page_source_respects_session_scope():
    module, cleanup = load_confluence_source_service_lightweight()
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

    class _Result:
        artifact_id = "a1"
        preview = "doc"
        content = "doc"
        text_ref = None
        parse_status = "completed"
        parse_error = None
        projected_to_text = True

    async def _fake_download(**kwargs):
        return _Result()

    def _fake_get_artifact(_artifact_id):
        return type(
            "Record",
            (),
            {
                "artifact_id": "a1",
                "file_id": "a1",
                "source_type": "confluence",
                "source_kind": "page_attachment",
                "filename": "a.pdf",
                "content_type": "application/pdf",
                "size": 1,
                "text_ref": "txt-ref",
                "context_ref": None,
                "digest_ref": None,
            },
        )

    module.artifact_storage.get_artifact = _fake_get_artifact

    try:
        without_session = await module.prepare_confluence_page_source("1", session_id=None, channel=_Channel(), downloader=_fake_download)
        assert without_session["manifest"]["context_ref"] is None
        assert without_session["manifest"]["digest_ref"] is None
        assert without_session["artifact_refs"]
        assert "session_scope_missing" in without_session["bundle"]["completeness_ledger"]["partial_reasons"]

        with_session = await module.prepare_confluence_page_source(
            "1",
            session_id="s1",
            channel=_Channel(),
            downloader=_fake_download,
            persist_fn=lambda **kwargs: {"context_ref": "ctx", "digest_ref": "dig"},
        )
        assert with_session["manifest"]["context_ref"] == "ctx"
        assert with_session["manifest"]["digest_ref"] == "dig"
        assert with_session["artifact_refs"]
    finally:
        cleanup()
