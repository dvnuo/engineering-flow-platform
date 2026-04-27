import types

import pytest

from tests._lightweight_source_service_loaders import load_confluence_source_service_lightweight


@pytest.mark.asyncio
async def test_confluence_bundle_has_artifact_refs_and_image_policy():
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
                return {"id": page_id, "title": "T", "body": {"storage": {"value": "<p>x</p>"}}}

            async def get_all_comments_with_ledger(self, page_id):
                return [], {"loaded": 0, "total": 0, "complete": True}

            async def get_all_attachments_with_ledger(self, page_id):
                return [
                    {"id": "i1", "title": "a.png", "metadata": {"mediaType": "image/png"}, "_links": {"download": "/i"}},
                    {"id": "d1", "title": "a.pdf", "metadata": {"mediaType": "application/pdf"}, "_links": {"download": "/d"}},
                ], {"loaded": 2, "total": 2, "complete": True}

            async def get_all_page_children_with_ledger(self, page_id):
                return [], {"loaded": 0, "total": 0, "complete": True}

            async def get_all_descendants_with_ledger(self, page_id):
                return [], {"loaded": 0, "total": 0, "complete": True, "partial_reasons": []}

        class _Res:
            artifact_id = "a1"
            preview = "doc"
            content = "doc"
            text_ref = "ctx://text/a1"
            parse_status = "completed"
            parse_error = None
            projected_to_text = True

        async def _fake_download(**kwargs):
            return _Res()

        module._test_storage.records["a1"] = types.SimpleNamespace(
            artifact_id="a1",
            text_ref="ctx://text/a1",
            context_ref=None,
            digest_ref=None,
        )
        out = await module.prepare_confluence_page_source(
            "1",
            session_id="s1",
            channel=_Channel(),
            downloader=_fake_download,
            persist_fn=lambda **kwargs: {"context_ref": "c", "digest_ref": "d"},
        )
        bundle = out["bundle"]
        ledger = bundle["completeness_ledger"]

        assert len(bundle["artifact_refs"]) == 1
        assert bundle["artifact_refs"][0]["artifact_id"] == "a1"
        assert ledger["attachments_loaded"] == 2
        assert ledger["binary_attachment_body_policy"] == "metadata_only"
        assert ledger["non_projectable_attachments_total"] == 1
        assert ledger["source_complete_including_binary_bodies"] is False
    finally:
        cleanup()
