import pytest


def _service_module():
    try:
        from src.github import source_service
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"github source service import unavailable in this environment: {exc}")
    return source_service


def test_empty_body_still_counts_as_body_loaded_for_generation_completeness():
    source_service = _service_module()
    ledger = source_service._build_asset_ledger(
        source_kind="issue",
        body_loaded=True,
        body_nonempty=False,
        comments_loaded=True,
        review_comments_loaded=True,
        asset_entries=[],
        partial_reasons=[],
    )

    assert ledger["body_loaded"] is True
    assert ledger["body_nonempty"] is False
    assert ledger["source_complete_for_generation"] is True
    assert ledger["source_complete"] is True


def test_projectable_assets_complete_marks_generation_complete():
    source_service = _service_module()
    ledger = source_service._build_asset_ledger(
        source_kind="pull_request",
        body_loaded=True,
        body_nonempty=True,
        comments_loaded=True,
        review_comments_loaded=True,
        asset_entries=[
            {
                "content_type": "text/plain",
                "filename": "a.txt",
                "parse_status": "completed",
                "projected_to_text": True,
                "text_ref": "ctx://text/1",
            }
        ],
        partial_reasons=[],
    )

    assert ledger["projectable_assets_total"] == 1
    assert ledger["text_assets_loaded"] == 1
    assert ledger["source_complete_for_generation"] is True


def test_non_projectable_assets_force_including_binary_false():
    source_service = _service_module()
    ledger = source_service._build_asset_ledger(
        source_kind="issue",
        body_loaded=True,
        body_nonempty=True,
        comments_loaded=True,
        review_comments_loaded=True,
        asset_entries=[
            {
                "content_type": "text/plain",
                "filename": "a.txt",
                "parse_status": "completed",
                "projected_to_text": True,
                "text_ref": "ctx://text/1",
            },
            {
                "content_type": "application/octet-stream",
                "filename": "blob.bin",
                "parse_status": "completed",
                "projected_to_text": False,
                "text_ref": None,
            },
        ],
        partial_reasons=[],
    )

    assert ledger["non_projectable_assets_total"] > 0
    assert ledger["source_complete_for_generation"] is True
    assert ledger["source_complete_including_binary_bodies"] is False
