import pytest

from tests._lightweight_source_service_loaders import load_confluence_source_service_lightweight


@pytest.mark.asyncio
async def test_confluence_projectable_attachment_parse_failure_lowers_completeness():
    module, cleanup = load_confluence_source_service_lightweight()
    try:

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
            artifact_id = None
            preview = None
            content = "[application/pdf: a.pdf]"
            text_ref = None
            parse_status = "failed"
            parse_error = "parse failed"
            projected_to_text = False

        async def _fake_download(**kwargs):
            return _Result()

        out = await module.prepare_confluence_page_source(
            "1",
            session_id="s1",
            channel=_Channel(),
            downloader=_fake_download,
            persist_fn=lambda **kwargs: {"context_ref": "c", "digest_ref": "d"},
        )
        ledger = out["bundle"]["completeness_ledger"]

        assert ledger["text_attachments_total"] == 1
        assert ledger["text_attachments_loaded"] == 0
        assert ledger["text_attachment_bodies_complete"] is False
        assert ledger["source_complete_for_generation"] is False
        assert ledger["source_complete"] is False
        assert any(str(r).startswith("attachment_text_processing_failed:a.pdf:parse_failed") for r in ledger["attachment_body_partial_reasons"])
    finally:
        cleanup()
