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
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
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
# here: pdf/docx/xlsx/pptx/csv go through their parsers, a zip is projected
# as its listing plus the text files inside, and any other extension whose
# bytes decode as UTF-8 goes through the text parser. Images (jpg, jpeg,
# png, webp, gif) are supported but off the default list because the default
# model has no vision; a deployment whose model can see adds them. A
# configured extension whose content the runtime cannot parse is still
# rejected at upload time.
# ---------------------------------------------------------------------------
UPLOAD_EXTENSIONS_ENV = "EFP_CHAT_UPLOAD_EXTENSIONS"
DEFAULT_UPLOAD_EXTENSIONS: Tuple[str, ...] = (
    "pdf", "docx", "xlsx", "csv", "txt", "log", "pptx", "zip", "md", "yaml", "yml", "json", "xml",
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
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
}

# Text formats with a dedicated MIME type; any other allowed extension whose
# bytes decode as UTF-8 is stored as text/plain and parsed by the text parser.
TEXT_EXTENSION_MIME_TYPES = {
    "txt": "text/plain",
    "log": "text/plain",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "rst": "text/x-rst",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "json": "application/json",
    "jsonl": "application/x-ndjson",
    "ndjson": "application/x-ndjson",
    "ipynb": "application/json",
    "yaml": "application/yaml",
    "yml": "application/yaml",
    "toml": "application/toml",
    "xml": "application/xml",
    "html": "text/html",
    "htm": "text/html",
    "ini": "text/plain",
    "cfg": "text/plain",
    "conf": "text/plain",
    "properties": "text/plain",
}

# Everything the runtime knows how to project, regardless of the allowlist.
KNOWN_UPLOAD_EXTENSIONS = frozenset(BINARY_EXTENSION_MIME_TYPES) | frozenset(TEXT_EXTENSION_MIME_TYPES)

# Encodings tried, in order, when a text attachment is decoded. UTF-8 first;
# GB18030 (a superset of GBK) second, because logs and exports written on
# Chinese Windows machines are routinely GBK and would otherwise be refused
# or turned into mojibake.
TEXT_ENCODINGS: Tuple[str, ...] = ("utf-8-sig", "gb18030")

_EXTENSION_TOKEN = re.compile(r"^[a-z0-9]+$")


def decode_text_bytes(raw: bytes) -> Tuple[str, str]:
    """Decode text bytes as UTF-8 or GB18030, falling back to UTF-8 with replacement.

    Returns the text and the encoding that produced it ("utf-8" when the
    fallback with replacement characters was used).
    """
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding), ("utf-8" if encoding == "utf-8-sig" else encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def looks_like_text(sample: bytes) -> bool:
    """Whether a leading sample of a file reads as text in a supported encoding.

    A NUL byte marks binary. Otherwise the sample must decode as UTF-8 or
    GB18030; a multi-byte character cut by the sample boundary is tolerated.
    """
    if not sample:
        return False
    if b"\x00" in sample:
        return False
    for encoding in TEXT_ENCODINGS:
        for candidate in (sample, sample[:-4]):
            try:
                candidate.decode(encoding)
                return True
            except UnicodeDecodeError:
                continue
    return False


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
    """Sanitize a user-provided filename for metadata and Content-Disposition.

    Keeps the member's own name for the file, whatever script it is in
    (Chinese names, spaces and brackets included), so the transcript shows the
    file they attached. Drops path components, control characters and leading
    dots, caps the length at 200 and falls back to a random name when nothing
    usable remains. Storage never uses this name (files are stored as
    ``<file_id><ext>``), so no further filesystem rules apply.

    Args:
        filename: Original filename from user

    Returns:
        Sanitized filename or random name if invalid
    """
    name = str(filename or "").replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if ord(c) >= 32 and c != "\x7f").strip().lstrip(".").strip()

    if not name:
        import uuid
        return f"file_{uuid.uuid4().hex[:8]}"

    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        if dot and stem and 0 < len(ext) <= 10:
            name = stem[: 200 - len(ext) - 1] + "." + ext
        else:
            name = name[:200]
    return name


ALLOWED_EXTENSIONS = {f".{ext}" for ext in DEFAULT_UPLOAD_EXTENSIONS}


def get_safe_extension(filename: str) -> str:
    """Get safe file extension: on the configured allowlist or a format the runtime knows.

    Args:
        filename: Original filename

    Returns:
        Safe extension with leading dot (e.g., ".jpg") or empty string
    """
    ext = Path(filename).suffix.lower()

    # Only allow alphanumeric extensions from allowlist
    if not re.match(r'^\.[a-z0-9]+$', ext):
        return ""
    bare = ext.lstrip(".")
    if bare in KNOWN_UPLOAD_EXTENSIONS or bare in allowed_upload_extensions():
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
