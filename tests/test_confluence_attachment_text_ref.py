import types

import pytest

from tests._lightweight_source_service_loaders import load_confluence_source_service_lightweight


@pytest.mark.asyncio
async def test_confluence_attachment_builds_text_ref_and_artifact_ref():
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

        class _Res:
            artifact_id = "a1"
            preview = "doc"
            content = "doc"
            text_ref = "txt-ref"
            parse_status = "completed"
            parse_error = None
            projected_to_text = True

        async def _fake_download(**kwargs):
            return _Res()

        module._test_storage.records["a1"] = types.SimpleNamespace(
            artifact_id="a1",
            text_ref="txt-ref",
            context_ref=None,
            digest_ref=None,
        )

        prepared = await module.prepare_confluence_page_source(
            "1",
            session_id="s1",
            channel=_Channel(),
            downloader=_fake_download,
            persist_fn=lambda **kwargs: {"context_ref": "ctx://conf", "digest_ref": "ctx://conf/d"},
        )
        bundle = prepared["bundle"]
        manifest = prepared["manifest"]

        assert bundle["attachments"][0]["text_ref"] == "txt-ref"
        assert bundle["artifact_refs"][0]["text_ref"] == "txt-ref"
        assert bundle["artifact_refs"][0]["context_ref"] == "ctx://conf"
        assert bundle["artifact_refs"][0]["digest_ref"] == "ctx://conf/d"
        assert manifest["context_ref"] == "ctx://conf"
        assert manifest["digest_ref"] == "ctx://conf/d"
    finally:
        cleanup()
