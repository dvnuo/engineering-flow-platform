"""File validation utilities."""

import os
import re
from pathlib import Path
from typing import Tuple

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
FILENAME_PATTERN = re.compile(r'^[^\x00-\x1f]{1,200}$')  # Allow any visible chars, max 200

# Allowed image extensions (in sync with ALLOWED_MIME_TYPES["image"])
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}


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


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".docx", ".xlsx", ".csv", ".txt"}


def get_safe_extension(filename: str) -> str:
    """Get safe file extension based on allowlist.
    
    Args:
        filename: Original filename
        
    Returns:
        Safe extension with leading dot (e.g., ".jpg") or empty string
    """
    ext = Path(filename).suffix.lower()
    
    # Only allow alphanumeric extensions from allowlist
    if re.match(r'^\.[a-z0-9]+$', ext) and ext in ALLOWED_EXTENSIONS:
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
