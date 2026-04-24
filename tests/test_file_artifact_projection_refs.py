from types import SimpleNamespace

import pytest

from src.file_artifacts.models import ArtifactRecord
from src.file_artifacts.service import register_existing_file_as_artifact, update_projection_from_parse_result
from src.file_artifacts.storage import storage as artifact_storage
from src.utils.file_parser import save_uploaded_file


async def _create_artifact(*, session_id: str | None):
    meta = await save_uploaded_file(
        b"hello artifact projection",
        "artifact.txt",
        session_id=session_id,
        content_type="text/plain",
    )
    return register_existing_file_as_artifact(
        meta.file_id,
        source_type="chat",
        source_kind="uploaded_file",
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_update_projection_persists_text_ref_when_session_scope_provided():
    artifact = await _create_artifact(session_id="s-artifact-text")
    parse_result = SimpleNamespace(
        markdown="## Title\n\nhello full text",
        blocks=[{"type": "paragraph"}],
        content_type="text/plain",
        filename="artifact.txt",
    )

    updated = update_projection_from_parse_result(
        artifact.artifact_id,
        parse_result,
        preview="## Title",
        persist_text_ref_session_id="s-artifact-text",
        persist_text_ref_kind="chat_uploaded_file_text",
        persist_text_ref_source_id=artifact.file_id,
        persist_text_ref_title="Chat uploaded file artifact.txt",
        persist_text_ref_metadata={"filename": "artifact.txt"},
    )

    assert isinstance(updated, ArtifactRecord)
    stored = artifact_storage.get_artifact(artifact.artifact_id)
    assert stored is not None
    assert stored.parse_status == "completed"
    assert stored.text_ref is not None
    assert stored.text_ref.startswith("ctx://context/s-artifact-text/chat_uploaded_file_text/")
    assert stored.full_markdown_chars == len(parse_result.markdown)


@pytest.mark.asyncio
async def test_update_projection_does_not_persist_text_ref_without_session_scope():
    artifact = await _create_artifact(session_id=None)
    parse_result = SimpleNamespace(
        markdown="plain text",
        blocks=[],
        content_type="text/plain",
        filename="artifact.txt",
    )

    update_projection_from_parse_result(
        artifact.artifact_id,
        parse_result,
        persist_text_ref_kind="chat_uploaded_file_text",
        persist_text_ref_source_id=artifact.file_id,
        persist_text_ref_title="Chat uploaded file artifact.txt",
    )

    stored = artifact_storage.get_artifact(artifact.artifact_id)
    assert stored is not None
    assert stored.text_ref is None
