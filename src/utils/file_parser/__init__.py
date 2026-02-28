"""File Parser - Unified parsing entry point.

Usage:
    from src.utils.file_parser import parse_file, upload_file
    
    # Upload
    metadata = await upload_file(content, "document.pdf", session_id)
    
    # Parse
    result = await parse_file(file_id)
"""

from .models import (
    Block,
    ParseResult,
    ImageConstraints,
    FileMetadata,
    FileValidationError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    StoredFileNotFoundError,
    ParseError,
)

from .validators import (
    validate_file_size,
    validate_content_type,
    validate_image_for_llm,
    sanitize_filename,
    is_image_file,
    get_mime_type,
)

from .storage import (
    init_storage,
    get_file_path,
    get_metadata,
    list_files,
    delete_file,
    save_uploaded_file,
)


# Lazy imports to avoid dependency issues
_async_modules = {}


def _get_image_module():
    """Lazy load image module."""
    if 'image' not in _async_modules:
        from . import image as _image
        _async_modules['image'] = _image
    return _async_modules['image']


def _get_pdf_module():
    """Lazy load PDF module."""
    if 'pdf' not in _async_modules:
        from . import pdf as _pdf
        _async_modules['pdf'] = _pdf
    return _async_modules['pdf']


def _get_docx_module():
    """Lazy load DOCX module."""
    if 'docx' not in _async_modules:
        from . import docx as _docx
        _async_modules['docx'] = _docx
    return _async_modules['docx']


def _get_excel_module():
    """Lazy load Excel/CSV module."""
    if 'excel' not in _async_modules:
        from . import excel as _excel
        _async_modules['excel'] = _excel
    return _async_modules['excel']


async def upload_file(
    content: bytes,
    filename: str,
    session_id: str = None,
    max_size_mb: int = 10
) -> FileMetadata:
    """Upload a file.
    
    Args:
        content: File content bytes
        filename: Original filename
        session_id: Session ID
        max_size_mb: Max file size in MB
        
    Returns:
        FileMetadata
        
    Raises:
        FileTooLargeError: If file exceeds size limit
        UnsupportedFileTypeError: If file type not allowed
    """
    # Validate size
    if not validate_file_size(len(content), max_size_mb):
        raise FileTooLargeError(f"File exceeds {max_size_mb}MB limit")
    
    # Validate type
    mime_type = _detect_mime_type(content, filename)
    if not validate_content_type(mime_type):
        raise UnsupportedFileTypeError(f"File type {mime_type} not allowed")
    
    # Save
    return await save_uploaded_file(content, filename, session_id)


async def parse_file(file_id: str, options: dict = None) -> ParseResult:
    """Parse a file by ID.
    
    Args:
        file_id: UUID of the file
        options: Parser options
        
    Returns:
        ParseResult
    """
    # Get file path
    path = get_file_path(file_id)
    metadata = get_metadata(file_id)
    
    content_type = metadata.content_type
    
    # Dispatch by type
    if content_type.startswith("image/"):
        image_mod = _get_image_module()
        return await image_mod.parse_image(str(path), options)
    
    if content_type == "application/pdf":
        pdf_mod = _get_pdf_module()
        return await pdf_mod.parse_pdf(str(path), options)
    
    if content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        docx_mod = _get_docx_module()
        return await docx_mod.parse_docx(str(path), options)
    
    if content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        excel_mod = _get_excel_module()
        return await excel_mod.parse_excel(str(path), options)
    
    if content_type == "text/csv":
        excel_mod = _get_excel_module()
        return await excel_mod.parse_csv(str(path), options)
    
    # Unsupported file type
    return ParseResult(
        success=False,
        content_type=content_type,
        file_id=file_id,
        filename=metadata.original_filename,
        error="Parser not implemented for this file type"
    )


async def preview_file(file_id: str, max_chars: int = 5000) -> dict:
    """Get a preview of parsed file.
    
    Args:
        file_id: UUID
        max_chars: Max characters to return
        
    Returns:
        Dict with preview and metadata
    """
    result = await parse_file(file_id)
    
    if not result.success:
        return {
            "success": False,
            "error": result.error
        }
    
    content = result.markdown
    truncated = len(content) > max_chars
    preview = content[:max_chars] if truncated else content
    
    return {
        "success": True,
        "preview": preview,
        "truncated": truncated,
        "total_chars": len(content)
    }


def _detect_mime_type(content: bytes, filename: str) -> str:
    """Detect MIME type."""
    from pathlib import Path
    ext = Path(filename).suffix.lower()
    
    # Use python-magic if available
    try:
        import magic
        detected = magic.from_buffer(content[:1024], mime=True)
        if detected and detected != "application/octet-stream":
            return detected
    except Exception:
        pass
    
    # Fallback: extension-based detection for all allowed types
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


# Export for convenience
def compress_image_for_llm(file_path: str, max_dimension: int = 1024, quality: int = 80) -> str:
    """Compress image and return base64."""
    image_mod = _get_image_module()
    return image_mod.compress_image_for_llm(file_path, max_dimension, quality)


def get_image_for_llm(file_path: str, constraints: ImageConstraints = None) -> str:
    """Get image as data URL for sending to LLM."""
    image_mod = _get_image_module()
    return image_mod.get_image_for_llm(file_path, constraints)


__all__ = [
    # Models
    "Block",
    "ParseResult",
    "ImageConstraints",
    "FileMetadata",
    "FileValidationError",
    "FileTooLargeError",
    "UnsupportedFileTypeError",
    "StoredFileNotFoundError",
    "ParseError",
    # Validators
    "validate_file_size",
    "validate_content_type",
    "validate_image_for_llm",
    "sanitize_filename",
    "is_image_file",
    "get_mime_type",
    # Storage
    "init_storage",
    "get_file_path",
    "get_metadata",
    "list_files",
    "delete_file",
    "save_uploaded_file",
    # Parser
    "parse_file",
    "upload_file",
    "preview_file",
    # Image
    "compress_image_for_llm",
    "get_image_for_llm",
]
