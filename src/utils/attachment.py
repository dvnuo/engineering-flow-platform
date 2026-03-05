"""Unified attachment processing module.

Downloads attachments from external sources (Jira, Confluence, etc.)
and processes them for LLM consumption.
"""

import re
import logging
import logging
import httpx
from dataclasses import dataclass
from typing import Optional, Dict, Any

from .file_parser import (
    save_uploaded_file,
    parse_file,
    get_file_path,
    compress_image_for_llm,
)

# Configure logging
import logging
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


async def download_and_process_attachment(
    url: str,
    session_id: str = None,
    options: dict = None
) -> AttachmentResult:
    """Download attachment from external URL and process for LLM.
    
    Args:
        url: Attachment URL (Jira/Confluence, etc.)
        session_id: Optional session ID
        options: Processing options
            - include_image_data: bool = True
            - max_image_size: int = 1024
            - max_text_chars: int = 5000
    
    Returns:
        AttachmentResult with content ready for LLM
    """
    options = options or {}
    include_image = options.get("include_image_data", True)
    max_image_size = options.get("max_image_size", 1024)
    max_text_chars = options.get("max_text_chars", 5000)
    
    # Download file
    content, content_type, filename = await _download_file(url)
    
    # Save to storage
    metadata = await save_uploaded_file(
        content=content,
        original_filename=filename,
        session_id=session_id,
        content_type=content_type
    )
    
    # Process based on type
    file_path = str(get_file_path(metadata.file_id))
    
    if content_type.startswith("image/") and include_image:
        # Compress and convert to base64
        try:
            content = compress_image_for_llm(file_path, max_dimension=max_image_size)
            content_format = "base64"
        except Exception as e:
            logger.warning(f"Failed to compress image: {e}")
            content = f"[Image: {filename}]"
            content_format = "text"
    elif content_type.startswith("text/") or _is_text_type(content_type):
        # Extract text
        try:
            result = await parse_file(metadata.file_id)
            content = result.markdown[:max_text_chars] if result.markdown else ""
            content_format = "text"
        except Exception as e:
            logger.warning(f"Failed to parse text: {e}")
            content = f"[Text file: {filename}]"
            content_format = "text"
    else:
        # Other file types - just return description
        content = f"[{content_type}: {filename}]"
        content_format = "text"
    
    return AttachmentResult(
        file_id=metadata.file_id,
        content_type=content_type,
        content=content,
        content_format=content_format,
        filename=filename,
        metadata={
            "size": metadata.size,
            "uploaded_at": metadata.uploaded_at,
        }
    )


async def _download_file(url: str) -> tuple[bytes, str, str]:
    """Download file from URL.
    
    Returns:
        (content, content_type, filename)
    """
    logger.info(f"Downloading attachment from: {url}")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        
        content = response.content
        
        # Get content type from header or detect
        content_type = response.headers.get("Content-Type", "")
        if ";" in content_type:
            content_type = content_type.split(";")[0].strip()
        
        # Get filename from header
        filename = _extract_filename(response.headers.get("Content-Disposition", ""))
        
        # Fallback: detect from URL
        if not filename:
            filename = url.split("/")[-1].split("?")[0]
        
        # Fallback: detect content type
        if not content_type or content_type == "application/octet-stream":
            content_type = _detect_content_type(filename, content)
        
        logger.info(f"Downloaded: {filename}, type: {content_type}, size: {len(content)}")
        
        return content, content_type, filename


def _extract_filename(header: str) -> str:
    """Extract filename from Content-Disposition header."""
    if not header:
        return ""
    
        match = re.search(r'filename[^;=\n]*=(([\'"]).*?\2|[^;\n]*)', header)
    if match:
        filename = match.group(1).strip('"\'')
        return filename
    return ""


def _is_text_type(content_type: str) -> bool:
    """Check if content type is text-based."""
    text_types = [
        "text/",
        "application/json",
        "application/xml",
        "application/javascript",
    ]
    return any(content_type.startswith(t) for t in text_types)


def _detect_content_type(filename: str, content: bytes) -> str:
    """Detect content type from filename or content."""
    try:
        import magic
        mime = magic.Magic(mime=True)
        return mime.from_buffer(content)
    except ImportError:
        # magic not installed, fall back to extension-based
        pass
    except Exception:
        pass
    
    # Fallback: extension-based
    ext = filename.lower().split(".")[-1] if "." in filename else ""
    mapping = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "pdf": "application/pdf",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain",
        "json": "application/json",
        "xml": "application/xml",
    }
    return mapping.get(ext, "application/octet-stream")


__all__ = [
    "download_and_process_attachment",
    "AttachmentResult",
]
