from __future__ import annotations

from pathlib import Path

TEXT_LIKE_MIME_TYPES = {
    "application/json",
    "application/xml",
    "text/xml",
    "application/yaml",
    "text/yaml",
    "application/x-yaml",
    "text/x-yaml",
}

PROJECTABLE_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "text/plain",
    *TEXT_LIKE_MIME_TYPES,
}

PROJECTABLE_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".json", ".xml", ".yaml", ".yml"}


def is_text_like_content_type(content_type: str) -> bool:
    normalized = str(content_type or "").lower()
    return normalized.startswith("text/") or normalized in TEXT_LIKE_MIME_TYPES


def can_project_to_text(content_type: str, filename: str) -> bool:
    normalized = str(content_type or "").lower()
    ext = Path(str(filename or "")).suffix.lower()
    if normalized.startswith("image/"):
        return False
    return normalized in PROJECTABLE_MIME_TYPES or normalized.startswith("text/") or ext in PROJECTABLE_EXTENSIONS


def infer_projection_kind(content_type: str, filename: str, parsed_markdown: str | None) -> str:
    normalized = str(content_type or "").lower()
    ext = Path(str(filename or "")).suffix.lower()
    if normalized.startswith("image/"):
        return "image"
    if normalized == "application/pdf" or ext == ".pdf":
        return "pdf_markdown"
    if normalized.endswith("wordprocessingml.document") or ext == ".docx":
        return "docx_markdown"
    if normalized.endswith("spreadsheetml.sheet") or ext == ".xlsx":
        return "xlsx_markdown"
    if normalized == "text/csv" or ext == ".csv":
        return "csv_text"
    if is_text_like_content_type(normalized) or ext in {".txt", ".json", ".xml", ".yaml", ".yml"}:
        return "text"
    return "markdown" if parsed_markdown else "metadata"
