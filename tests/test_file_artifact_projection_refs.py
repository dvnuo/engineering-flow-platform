from types import SimpleNamespace

import pytest

from tests._import_helpers import load_module_from_repo_path
from tests._lightweight_file_parser_loader import load_file_parser_lightweight


@pytest.fixture
def _lightweight_file_artifacts_stack():
    file_parser_module, parser_cleanup = load_file_parser_lightweight()
    try:
        models_module = load_module_from_repo_path("src.file_artifacts.models", "src/file_artifacts/models.py")
        storage_module = load_module_from_repo_path("src.file_artifacts.storage", "src/file_artifacts/storage.py")
        service_module = load_module_from_repo_path("src.file_artifacts.service", "src/file_artifacts/service.py")
        yield file_parser_module, models_module, storage_module, service_module
    finally:
        parser_cleanup()


async def _create_artifact(service_module, file_parser_module, *, session_id: str | None):
    meta = await file_parser_module.save_uploaded_file(
        b"hello artifact projection",
        "artifact.txt",
        session_id=session_id,
        content_type="text/plain",
    )
    return service_module.register_existing_file_as_artifact(
        meta.file_id,
        source_type="chat",
        source_kind="uploaded_file",
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_update_projection_persists_text_ref_when_session_scope_provided(_lightweight_file_artifacts_stack):
    file_parser_module, models_module, storage_module, service_module = _lightweight_file_artifacts_stack

    artifact = await _create_artifact(service_module, file_parser_module, session_id="s-artifact-text")
    parse_result = SimpleNamespace(
        markdown="## Title\n\nhello full text",
        blocks=[{"type": "paragraph"}],
        content_type="text/plain",
        filename="artifact.txt",
    )

    updated = service_module.update_projection_from_parse_result(
        artifact.artifact_id,
        parse_result,
        preview="## Title",
        persist_text_ref_session_id="s-artifact-text",
        persist_text_ref_kind="chat_uploaded_file_text",
        persist_text_ref_source_id=artifact.file_id,
        persist_text_ref_title="Chat uploaded file artifact.txt",
        persist_text_ref_metadata={"filename": "artifact.txt"},
    )

    assert isinstance(updated, models_module.ArtifactRecord)
    stored = storage_module.storage.get_artifact(artifact.artifact_id)
    assert stored is not None
    assert stored.parse_status == "completed"
    assert stored.text_ref is not None
    assert stored.text_ref.startswith("ctx://context/s-artifact-text/chat_uploaded_file_text/")
    assert stored.full_markdown_chars == len(parse_result.markdown)


@pytest.mark.asyncio
async def test_update_projection_does_not_persist_text_ref_without_session_scope(_lightweight_file_artifacts_stack):
    file_parser_module, _models_module, storage_module, service_module = _lightweight_file_artifacts_stack

    artifact = await _create_artifact(service_module, file_parser_module, session_id=None)
    parse_result = SimpleNamespace(
        markdown="plain text",
        blocks=[],
        content_type="text/plain",
        filename="artifact.txt",
    )

    service_module.update_projection_from_parse_result(
        artifact.artifact_id,
        parse_result,
        persist_text_ref_kind="chat_uploaded_file_text",
        persist_text_ref_source_id=artifact.file_id,
        persist_text_ref_title="Chat uploaded file artifact.txt",
    )

    stored = storage_module.storage.get_artifact(artifact.artifact_id)
    assert stored is not None
    assert stored.text_ref is None
