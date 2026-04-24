"""Unified attachment processing module.

Downloads attachments from external sources (Jira, Confluence, etc.)
and processes them for LLM consumption.
"""

import re
import logging
import httpx
from dataclasses import dataclass
from typing import Optional, Dict, Any

from src.file_artifacts import can_project_to_text
from src.context_blob_store import put_text
from src.file_artifacts.service import attach_text_ref_to_artifact, register_existing_file_as_artifact, update_projection_from_parse_result
from src.file_artifacts.storage import storage as artifact_storage
from .file_parser import (
    save_uploaded_file,
    parse_file,
    get_file_path,
    compress_image_for_llm,
)

logger = logging.getLogger(__name__)


@dataclass
class AttachmentResult:
    """Result of attachment processing."""
    file_id: str
    content_type: str
    content: str  # base64 (image) or text content
    content_format: str  # "base64" or "text"
    filename: str
    metadata: Dict[str, Any]
    artifact_id: Optional[str] = None
    projection_kind: Optional[str] = None
    preview: Optional[str] = None
    text_ref: Optional[str] = None


async def download_and_process_attachment(
    url: str,
    session_id: str = None,
    options: dict = None,
    auth_header: dict = None,
    source_type: str = "jira",
    source_kind: str = "issue_attachment",
    source_locator: str | None = None,
    provider_metadata: dict | None = None,
    persist_text_ref_session_id: Optional[str] = None,
    persist_text_ref_kind: Optional[str] = None,
    persist_text_ref_source_id: Optional[str] = None,
    persist_text_ref_title: Optional[str] = None,
    persist_text_ref_metadata: Optional[dict] = None,
) -> AttachmentResult:
    options = options or {}
    include_image = options.get("include_image_data", True)
    max_image_size = options.get("max_image_size", 1024)
    max_text_chars = options.get("max_text_chars", 5000)
    prefer_text_for_images = options.get("prefer_text_for_images", False)
    vision_enabled = options.get("vision_enabled", False)
    ocr_engine = options.get("ocr_engine", "paddleocr")

    content_bytes, content_type, filename = await _download_file(url, auth_header=auth_header)

    metadata = await save_uploaded_file(
        content=content_bytes,
        original_filename=filename,
        session_id=session_id,
        content_type=content_type,
    )
    artifact = register_existing_file_as_artifact(
        metadata.file_id,
        source_type=source_type,
        source_kind=source_kind,
        source_locator=source_locator,
        session_id=session_id,
        provider_metadata=provider_metadata,
    )

    file_path = str(get_file_path(metadata.file_id))
    projection_kind = None
    preview = None
    text_ref = None

    should_parse_image = content_type.startswith("image/") and (prefer_text_for_images or vision_enabled)
    should_parse_non_image = not content_type.startswith("image/") and can_project_to_text(content_type, filename)

    if should_parse_image or should_parse_non_image:
        try:
            parsed = await parse_file(
                metadata.file_id,
                options={
                    "vision_enabled": vision_enabled,
                    "ocr_engine": ocr_engine,
                },
            )
            if getattr(parsed, "success", False):
                full_text = (getattr(parsed, "markdown", "") or "")
                preview = full_text[:max_text_chars]
                content = preview
                content_format = "text"
                updated = update_projection_from_parse_result(artifact.artifact_id, parsed, preview=full_text[:2000])
                if updated:
                    artifact = updated
                if (
                    persist_text_ref_session_id
                    and persist_text_ref_kind
                    and persist_text_ref_source_id
                    and persist_text_ref_title
                    and full_text
                ):
                    text_ref = put_text(
                        session_id=persist_text_ref_session_id,
                        kind=persist_text_ref_kind,
                        source_id=persist_text_ref_source_id,
                        title=persist_text_ref_title,
                        content=full_text,
                        metadata=persist_text_ref_metadata or {},
                    )
                    updated_refs = attach_text_ref_to_artifact(artifact.artifact_id, text_ref, full_markdown_chars=len(full_text))
                    if updated_refs:
                        artifact = updated_refs
                projection_kind = artifact.projection_kind
            else:
                content = f"[{content_type}: {filename}]"
                content_format = "text"
                artifact_storage.update_artifact_status(
                    artifact.artifact_id,
                    parse_status="failed",
                    parse_error=str(getattr(parsed, "error", "parse failed")),
                )
        except Exception as e:
            logger.warning(f"Failed to parse attachment: {e}")
            content = f"[{content_type}: {filename}]"
            content_format = "text"
            artifact_storage.update_artifact_status(artifact.artifact_id, parse_status="failed", parse_error=str(e))
    elif content_type.startswith("image/"):
        artifact_storage.update_artifact_status(artifact.artifact_id, parse_status="skipped")
        if include_image:
            try:
                content = compress_image_for_llm(file_path, max_dimension=max_image_size)
                content_format = "base64"
            except Exception as e:
                logger.warning(f"Failed to compress image: {e}")
                content = f"[Image: {filename}]"
                content_format = "text"
        else:
            content = f"[Image: {filename}]"
            content_format = "text"
    else:
        content = f"[{content_type}: {filename}]"
        content_format = "text"
        artifact_storage.update_artifact_status(artifact.artifact_id, parse_status="skipped")

    return AttachmentResult(
        file_id=metadata.file_id,
        content_type=content_type,
        content=content,
        content_format=content_format,
        filename=filename,
        metadata={
            "size": metadata.size,
            "uploaded_at": metadata.uploaded_at,
        },
        artifact_id=artifact.artifact_id,
        projection_kind=projection_kind or artifact.projection_kind,
        preview=preview or artifact.preview,
        text_ref=text_ref or artifact.text_ref,
    )


async def _download_file(url: str, auth_header: dict = None) -> tuple[bytes, str, str]:
    logger.info(f"Downloading attachment from: {url}")
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        headers = auth_header or {}
        response = await client.get(url, headers=headers)
        if response.status_code in (301, 302, 303, 307, 308):
            redirect_url = response.headers.get("location", "")
            if not redirect_url:
                raise ValueError("Redirect response missing Location header")
            safe_url = redirect_url.split("?")[0] if "?" in redirect_url else redirect_url
            logger.info(f"Following redirect to: {safe_url}")
            async with httpx.AsyncClient(timeout=30.0) as client2:
                response = await client2.get(redirect_url)
                response.raise_for_status()
        else:
            response.raise_for_status()

        content = response.content
        content_type = response.headers.get("Content-Type", "")
        if ";" in content_type:
            content_type = content_type.split(";")[0].strip()
        filename = _extract_filename(response.headers.get("Content-Disposition", ""))
        if not filename:
            filename = url.split("/")[-1].split("?")[0]
        if not content_type or content_type == "application/octet-stream":
            content_type = _detect_content_type(filename, content)
        logger.info(f"Downloaded: {filename}, type: {content_type}, size: {len(content)}")
        return content, content_type, filename


def _extract_filename(header: str) -> str:
    if not header:
        return ""
    match = re.search(r'filename[^;=\n]*=(([\'"]).*?\2|[^;\n]*)', header)
    if match:
        return match.group(1).strip('"\'')
    return ""


def _detect_content_type(filename: str, content: bytes) -> str:
    try:
        import magic
        mime = magic.Magic(mime=True)
        return mime.from_buffer(content)
    except Exception:
        pass
    ext = filename.lower().split(".")[-1] if "." in filename else ""
    mapping = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "pdf": "application/pdf",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
        "txt": "text/plain",
        "json": "application/json",
        "xml": "application/xml",
    }
    return mapping.get(ext, "application/octet-stream")


__all__ = ["download_and_process_attachment", "AttachmentResult"]
