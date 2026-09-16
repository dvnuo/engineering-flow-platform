"""File validation utilities."""

import os
import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .models import ImageConstraints


# Allowed MIME types (can be configured)
ALLOWED_MIME_TYPES = {
    "image": ["image/jpeg", "image/png", "image/webp", "image/gif"],
    "document": [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ],
    "text": ["text/csv", "text/plain"],
}

# Filename pattern: alphanumeric, dot, underscore, hyphen, 1-200 chars
FILENAME_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$')

# Allowed image extensions (in sync with ALLOWED_MIME_TYPES["image"])
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}


# ---------------------------------------------------------------------------
# Chat attachment allowlist
#
# EFP_CHAT_UPLOAD_EXTENSIONS lists the file extensions the Portal chat composer
# accepts (comma-separated, case-insensitive, leading dots optional). The
# Portal renders the same setting into its file picker and hands it to every
# agent pod, so both ends agree on what "ask the model about this file" can
# carry. Only formats the runtime can actually hand to the model make sense
# here: the images below go to the model as images; pdf/docx/xlsx/csv and any
# UTF-8 text format are projected to text. A configured extension whose
# content the runtime cannot parse is still rejected at upload time.
# ---------------------------------------------------------------------------
UPLOAD_EXTENSIONS_ENV = "EFP_CHAT_UPLOAD_EXTENSIONS"
DEFAULT_UPLOAD_EXTENSIONS: Tuple[str, ...] = (
    "jpg", "jpeg", "png", "webp", "gif", "pdf", "docx", "xlsx", "csv", "txt",
)

# User-facing per-file cap shared with the Portal (same env, same default);
# src/gateway/server.py adds transport headroom on top for client_max_size.
MAX_UPLOAD_MB_ENV = "EFP_MAX_UPLOAD_MB"
DEFAULT_MAX_UPLOAD_MB = 25

# Binary formats recognised by content signature (see _detect_mime_type).
BINARY_EXTENSION_MIME_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# Text formats with a dedicated MIME type; any other allowed extension whose
# bytes decode as UTF-8 is stored as text/plain and parsed by the text parser.
TEXT_EXTENSION_MIME_TYPES = {
    "txt": "text/plain",
    "log": "text/plain",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "json": "application/json",
    "yaml": "application/yaml",
    "yml": "application/yaml",
    "xml": "application/xml",
    "html": "text/html",
    "htm": "text/html",
}

_EXTENSION_TOKEN = re.compile(r"^[a-z0-9]+$")


def parse_upload_extensions(raw: Optional[str]) -> List[str]:
    """Normalize a comma/semicolon/whitespace separated extension list.

    Lowercases, strips leading dots and surrounding whitespace, drops tokens
    that are not plain alphanumerics and de-duplicates while keeping the
    configured order. An empty or all-invalid value yields the defaults so a
    typo in the env var never locks the chatbox.
    """
    seen = set()
    result: List[str] = []
    for token in re.split(r"[,;\s]+", str(raw or "")):
        ext = token.strip().lower().lstrip(".")
        if not ext or not _EXTENSION_TOKEN.match(ext) or ext in seen:
            continue
        seen.add(ext)
        result.append(ext)
    return result or list(DEFAULT_UPLOAD_EXTENSIONS)


def allowed_upload_extensions() -> List[str]:
    """Extensions the chat upload endpoint accepts (from env or defaults)."""
    return parse_upload_extensions(os.getenv(UPLOAD_EXTENSIONS_ENV))


def file_extension(filename: str) -> str:
    """Lowercase extension of ``filename`` without the dot ('' when absent)."""
    return Path(str(filename or "")).suffix.lower().lstrip(".")


def is_upload_extension_allowed(filename: str, allowed: Optional[Iterable[str]] = None) -> bool:
    ext = file_extension(filename)
    if not ext:
        return False
    return ext in set(allowed if allowed is not None else allowed_upload_extensions())


def resolve_max_upload_mb() -> int:
    """User-facing per-file upload cap in MB (EFP_MAX_UPLOAD_MB, default 25)."""
    raw = os.getenv(MAX_UPLOAD_MB_ENV, str(DEFAULT_MAX_UPLOAD_MB))
    try:
        mb = int(str(raw).strip())
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_UPLOAD_MB
    return mb if mb > 0 else DEFAULT_MAX_UPLOAD_MB


def is_supported_upload_mime(mime_type: str) -> bool:
    """Whether the runtime can hand a file of this type to the model.

    Images go as images, pdf/docx/xlsx/csv through their parsers, and every
    ``text/*`` or text-like application type through the generic text parser.
    """
    normalized = str(mime_type or "").lower().split(";")[0].strip()
    if not normalized:
        return False
    if validate_content_type(normalized):
        return True
    if normalized.startswith("text/"):
        return True
    return normalized in set(TEXT_EXTENSION_MIME_TYPES.values())


def validate_file_size(size: int, max_size_mb: int = 10) -> bool:
    """Check if file size is within limit.
    
    Args:
        size: File size in bytes
        max_size_mb: Maximum size in MB
        
    Returns:
        True if within limit
    """
    return size <= max_size_mb * 1024 * 1024


def validate_content_type(mime_type: str, allowed_types: list = None) -> bool:
    """Check if MIME type is allowed.
    
    Args:
        mime_type: MIME type string (e.g., "image/jpeg")
        allowed_types: List of allowed types (supports wildcard *)
        
    Returns:
        True if allowed
    """
    if allowed_types is None:
        allowed_types = []
        for types in ALLOWED_MIME_TYPES.values():
            allowed_types.extend(types)
    
    category = mime_type.split("/")[0]
    
    for allowed in allowed_types:
        if allowed == mime_type:
            return True
        if allowed.endswith("/*") and allowed.split("/")[0] == category:
            return True
    
    return False


def validate_image_for_llm(
    file_path: str,
    constraints: ImageConstraints = None
) -> Tuple[bool, str]:
    """Validate image can be sent to LLM.
    
    Args:
        file_path: Path to image file
        constraints: Image constraints (uses defaults if None)
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if constraints is None:
        constraints = ImageConstraints()
    
    # Check size
    try:
        size = os.path.getsize(file_path)
    except OSError:
        return False, "Cannot read file"
    
    if size > constraints.max_size_mb * 1024 * 1024:
        return False, f"File too large: {size / 1024 / 1024:.1f}MB > {constraints.max_size_mb}MB"
    
    # Check format
    ext = Path(file_path).suffix.lower().lstrip(".")
    if ext not in constraints.allowed_formats:
        return False, f"Unsupported format: {ext}. Allowed: {constraints.allowed_formats}"
    
    return True, ""


def sanitize_filename(filename: str) -> str:
    """Sanitize user-provided filename.
    
    Rules:
    - Only allow letters, digits, dots, underscores, hyphens
    - Must start with letter or digit
    - Max 200 characters
    - Strip control characters
    
    Args:
        filename: Original filename from user
        
    Returns:
        Sanitized filename or random name if invalid
    """
    # Extract name only (remove path)
    name = Path(filename).name
    
    # Strip control characters
    name = ''.join(c for c in name if ord(c) >= 32)
    
    # Check if valid (only reject empty or control chars)
    if not name or not FILENAME_PATTERN.match(name):
        import uuid
        return f"file_{uuid.uuid4().hex[:8]}"
    
    return name


ALLOWED_EXTENSIONS = {f".{ext}" for ext in DEFAULT_UPLOAD_EXTENSIONS}


def get_safe_extension(filename: str) -> str:
    """Get safe file extension based on the configured allowlist.

    Args:
        filename: Original filename

    Returns:
        Safe extension with leading dot (e.g., ".jpg") or empty string
    """
    ext = Path(filename).suffix.lower()

    # Only allow alphanumeric extensions from allowlist
    if re.match(r'^\.[a-z0-9]+$', ext) and ext.lstrip(".") in allowed_upload_extensions():
        return ext

    return ""


def is_image_file(filename: str) -> bool:
    """Check if file is an image based on extension.
    
    Args:
        filename: File name
        
    Returns:
        True if image extension
    """
    ext = Path(filename).suffix.lower().lstrip(".")
    return ext in IMAGE_EXTENSIONS


def get_mime_type(file_path: str) -> str:
    """Get MIME type from file.
    
    Args:
        file_path: Path to file
        
    Returns:
        MIME type string
    """
    # Use python-magic if available
    try:
        import magic
        with open(file_path, "rb") as f:
            detected = magic.from_buffer(f.read(1024), mime=True)
            if detected and detected != "application/octet-stream":
                return detected
    except Exception:
        pass
    
    # Fallback to extension-based guess
    ext = Path(file_path).suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv",
        ".txt": "text/plain",
    }
    return mime_map.get(ext, "application/octet-stream")
