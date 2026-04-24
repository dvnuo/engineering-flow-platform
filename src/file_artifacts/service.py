from __future__ import annotations

from typing import Any, Dict, Optional

from src.context_blob_store import put_text
from src.utils.file_parser import get_metadata

from .capabilities import infer_projection_kind
from .models import ArtifactBinding, ArtifactRecord
from .storage import storage


def register_existing_file_as_artifact(
    file_id: str,
    *,
    source_type: str,
    source_kind: str,
    source_locator: Optional[str] = None,
    session_id: Optional[str] = None,
    provider_metadata: Optional[Dict[str, Any]] = None,
) -> ArtifactRecord:
    metadata = get_metadata(file_id)
    existing = storage.get_artifact(file_id)
    record = ArtifactRecord(
        artifact_id=file_id,
        file_id=file_id,
        source_type=source_type,
        source_kind=source_kind,
        source_locator=source_locator,
        filename=metadata.original_filename,
        content_type=metadata.content_type,
        size=int(metadata.size or 0),
        session_id=session_id or metadata.session_id,
        parse_status=existing.parse_status if existing else "pending",
        parse_error=existing.parse_error if existing else None,
        projection_kind=existing.projection_kind if existing else None,
        preview=existing.preview if existing else None,
        chunk_count=existing.chunk_count if existing else 0,
        total_chars=existing.total_chars if existing else 0,
        text_ref=existing.text_ref if existing else None,
        context_ref=existing.context_ref if existing else None,
        digest_ref=existing.digest_ref if existing else None,
        full_markdown_chars=existing.full_markdown_chars if existing else 0,
        provider_metadata={**(existing.provider_metadata if existing else {}), **(provider_metadata or {})},
    )
    return storage.upsert_artifact(record)


def bind_artifact_to_session(artifact_id: str, session_id: str, role: str = "attachment") -> ArtifactBinding:
    return storage.bind_artifact(ArtifactBinding(artifact_id=artifact_id, scope_type="session", scope_id=session_id, role=role))


def bind_artifact_to_source_bundle(artifact_id: str, bundle_scope_id: str, role: str = "reference") -> ArtifactBinding:
    return storage.bind_artifact(ArtifactBinding(artifact_id=artifact_id, scope_type="source_bundle", scope_id=bundle_scope_id, role=role))


def persist_artifact_text_ref(
    *,
    artifact_id: str,
    markdown: str,
    session_id: str | None,
    kind: str | None,
    source_id: str | None,
    title: str | None,
    metadata: dict | None,
) -> Optional[ArtifactRecord]:
    if not (session_id and kind and source_id and title and markdown):
        return None
    text_ref = put_text(
        session_id=session_id,
        kind=kind,
        source_id=source_id,
        title=title,
        content=markdown,
        metadata=metadata or {},
    )
    return storage.update_artifact_references(
        artifact_id,
        text_ref=text_ref,
        full_markdown_chars=len(markdown),
    )


def update_projection_from_parse_result(
    artifact_id: str,
    parse_result,
    preview: Optional[str] = None,
    *,
    persist_text_ref_session_id: str | None = None,
    persist_text_ref_kind: str | None = None,
    persist_text_ref_source_id: str | None = None,
    persist_text_ref_title: str | None = None,
    persist_text_ref_metadata: dict | None = None,
) -> Optional[ArtifactRecord]:
    markdown = getattr(parse_result, "markdown", "") or ""
    block_count = len(getattr(parse_result, "blocks", []) or [])
    projection_kind = infer_projection_kind(
        getattr(parse_result, "content_type", ""),
        getattr(parse_result, "filename", ""),
        markdown,
    )
    storage.update_artifact_projection(
        artifact_id,
        projection_kind=projection_kind,
        preview=preview if preview is not None else markdown[:2000],
        chunk_count=block_count,
        total_chars=len(markdown),
    )
    storage.update_artifact_references(
        artifact_id,
        full_markdown_chars=len(markdown),
    )
    persist_artifact_text_ref(
        artifact_id=artifact_id,
        markdown=markdown,
        session_id=persist_text_ref_session_id,
        kind=persist_text_ref_kind,
        source_id=persist_text_ref_source_id,
        title=persist_text_ref_title,
        metadata=persist_text_ref_metadata,
    )
    return storage.update_artifact_status(artifact_id, parse_status="completed")


def attach_text_ref_to_artifact(artifact_id: str, text_ref: str, *, full_markdown_chars: Optional[int] = None) -> Optional[ArtifactRecord]:
    return storage.update_artifact_references(
        artifact_id,
        text_ref=text_ref,
        full_markdown_chars=full_markdown_chars,
    )


def attach_source_refs_to_artifact(
    artifact_id: str,
    *,
    context_ref: Optional[str] = None,
    digest_ref: Optional[str] = None,
) -> Optional[ArtifactRecord]:
    return storage.update_artifact_references(
        artifact_id,
        context_ref=context_ref,
        digest_ref=digest_ref,
    )


def build_artifact_ref_dict(record: ArtifactRecord, *, text_ref: str | None = None) -> dict:
    effective_text_ref = text_ref if text_ref is not None else record.text_ref
    return {
        "artifact_id": record.artifact_id,
        "file_id": record.file_id,
        "filename": record.filename,
        "content_type": record.content_type,
        "source_type": record.source_type,
        "source_kind": record.source_kind,
        "source_locator": record.source_locator,
        "projection_kind": record.projection_kind,
        "preview": record.preview,
        "text_ref": effective_text_ref,
        "context_ref": record.context_ref,
        "digest_ref": record.digest_ref,
    }
