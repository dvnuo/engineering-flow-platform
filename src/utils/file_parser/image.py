import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
"""Image parser implementation."""

import base64
import io
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Lazy imports for optional dependencies
_PIL = None
_paddleocr = None

from .models import Block, ParseResult, ImageConstraints
from .validators import validate_image_for_llm, get_mime_type


def _get_pil():
    """Lazy load PIL.Image."""
    global _PIL
    if _PIL is None:
        from PIL import Image
        _PIL = Image
    return _PIL


def _get_paddleocr():
    """Get or create cached PaddleOCR instance."""
    global _paddleocr
    if _paddleocr is None:
        try:
            from paddleocr import PaddleOCR
            _paddleocr = PaddleOCR(use_angle_cls=True, lang='ch_en', show_log=False)
        except ImportError:
            pass
    return _paddleocr


# Default constraints
DEFAULT_IMAGE_CONSTRAINTS = ImageConstraints()


async def parse_image(
    file_path: str,
    options: Dict = None,
    constraints: ImageConstraints = None
) -> ParseResult:
    """Parse image file.
    
    Strategy:
    1. Vision LLM (if enabled and available)
    2. OCR fallback (PaddleOCR or Tesseract)
    
    Args:
        file_path: Path to image file
        options: Parser options
        constraints: Image constraints for LLM
        
    Returns:
        ParseResult
    """
    start_time = time.time()
    options = options or {}
    constraints = constraints or DEFAULT_IMAGE_CONSTRAINTS
    
    file_id = Path(file_path).stem.split("_")[0]
    filename = Path(file_path).name
    
    # Try Vision LLM first (validate LLM constraints only when using vision)
    vision_enabled = options.get("vision_enabled", False)
    if vision_enabled:
        valid, error = validate_image_for_llm(file_path, constraints)
        if valid:
            try:
                result = await parse_image_with_vision(file_path, options)
                if result.success:
                    result.parse_time_ms = int((time.time() - start_time) * 1000)
                    return result
            except Exception as e:
                import logging
                logging.warning(f"Vision parsing failed, falling back to OCR: {e}")
        else:
            import logging
            logging.debug(f"Image validation failed: {error}")
    
    # OCR fallback (no LLM-specific constraints needed)
    try:
        result = await parse_image_with_ocr(file_path, options)
        result.parse_time_ms = int((time.time() - start_time) * 1000)
        return result
    except Exception as e:
        return ParseResult(
            success=False,
            content_type=get_mime_type(file_path),
            file_id=file_id,
            filename=filename,
            error=f"OCR failed: {str(e)}"
        )


async def parse_image_with_vision(file_path: str, options: Dict) -> ParseResult:
    """Parse image using Vision LLM.
    
    Note: This requires an LLM client to be configured.
    
    Args:
        file_path: Path to image
        options: Options including vision_enabled, prompt
        
    Returns:
        ParseResult with vision-generated description
    """
    from src.agents.core import Agent
    import uuid
    
    file_id = Path(file_path).stem.split("_")[0]
    filename = Path(file_path).name
    
    try:
        # Get image as data URL for LLM
        image_data_url = get_image_for_llm(file_path)
    except Exception as e:
        return ParseResult(
            success=False,
            content_type=get_mime_type(file_path),
            file_id=file_id,
            filename=filename,
            error=f"Failed to prepare image: {str(e)}"
        )
    
    # Create a prompt to analyze the image
    prompt = options.get("prompt", "Describe this image in detail. Extract all visible text, diagrams, charts, or other content.")
    
    # Use a temporary session for the vision request
    session_id = f"vision_{uuid.uuid4().hex[:8]}"
    
    try:
        # Create agent and run with image
        agent = Agent(
            session_id=session_id,
            system_prompt="You are an expert at analyzing images. Provide detailed, accurate descriptions of images and extract any text visible in them."
        )
        
        # Run with the image
        response = await agent.process(
            message=prompt,
            session_id=session_id,
            attached_images=[image_data_url]
        )
        
        # Extract the response
        if response.get("response"):
            description = response.get("response") or "No description generated"
            
            # Create result with the vision description
            return ParseResult(
                success=True,
                content_type=get_mime_type(file_path),
                file_id=file_id,
                filename=filename,
                content=description,
                markdown=f"## Image Analysis\n\n{description}",
                blocks=[
                    Block(
                        type="text",
                        content=description,
                        page=1,
                        index=1,
                        method="vision",
                        chunk_id=f"{file_id}_vision_1_1",
                        extracted_at=datetime.utcnow().isoformat() + "Z",
                        confidence=0.9
                    )
                ]
            )
        else:
            return ParseResult(
                success=False,
                content_type=get_mime_type(file_path),
                file_id=file_id,
                filename=filename,
                error=f"Vision LLM failed: {response.error}"
            )
    except Exception as e:
        return ParseResult(
            success=False,
            content_type=get_mime_type(file_path),
            file_id=file_id,
            filename=filename,
            error=f"Vision parsing error: {str(e)}"
        )


async def parse_image_with_ocr(file_path: str, options: Dict) -> ParseResult:
    """Parse image using OCR.
    
    Args:
        file_path: Path to image
        options: Options with ocr_engine
        
    Returns:
        ParseResult with OCR extracted text
    """
    engine = options.get("ocr_engine", "paddleocr")
    file_id = Path(file_path).stem.split("_")[0]
    filename = Path(file_path).name
    
    ocr_error = None
    if engine == "paddleocr":
        paddleocr = _get_paddleocr()
        if paddleocr is None:
            ocr_error = "PaddleOCR not installed, and Tesseract may also be unavailable"
        blocks = await _parse_with_paddleocr(file_path, file_id)
    else:
        blocks = await _parse_with_tesseract(file_path, file_id)
    
    # Check if OCR returned any results
    if not blocks:
        error_msg = ocr_error if ocr_error else "OCR found no text in image"
        return ParseResult(
            success=False,
            content_type=get_mime_type(file_path),
            file_id=file_id,
            filename=filename,
            error=error_msg
        )
    
    markdown = "\n".join(b.content for b in blocks if b.content.strip())
    
    return ParseResult(
        success=True,
        content_type=get_mime_type(file_path),
        file_id=file_id,
        filename=filename,
        markdown=markdown,
        blocks=blocks
    )


async def _parse_with_paddleocr(file_path: str, file_id: str) -> List[Block]:
    """Parse image with PaddleOCR.
    
    Args:
        file_path: Path to image
        file_id: File ID for chunk_id
        
    Returns:
        List of blocks
    """
    ocr = _get_paddleocr()
    if ocr is None:
        return await _parse_with_tesseract(file_path, file_id)
    
    results = ocr.ocr(file_path, cls=True)
    
    if not results or not results[0]:
        return []
    
    blocks = []
    for page_idx, page in enumerate(results):
        for line_idx, line in enumerate(page):
            box, (text, confidence) = line
            blocks.append(Block(
                chunk_id=f"{file_id}_img_{page_idx + 1}_{line_idx + 1}",
                type="paragraph",
                content=text,
                method="paddleocr",
                confidence=confidence,
                bbox=box,
                extracted_at=datetime.now().isoformat()
            ))
    
    return blocks


async def _parse_with_tesseract(file_path: str, file_id: str) -> List[Block]:
    """Parse image with Tesseract.
    
    Args:
        file_path: Path to image
        file_id: File ID for chunk_id
        
    Returns:
        List of blocks
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return []
    
    img = _get_pil().open(file_path)
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    
    blocks = []
    texts = data.get("text", [])
    confs = data.get("conf", [])
    lefts = data.get("left", [])
    tops = data.get("top", [])
    widths = data.get("width", [])
    heights = data.get("height", [])
    
    for idx, (text, conf) in enumerate(zip(texts, confs)):
        # Convert confidence to float safely (Tesseract may return strings)
        try:
            conf_value = float(conf) if conf else -1
        except (TypeError, ValueError):
            conf_value = -1
        
        # Skip empty text or invalid/negative confidence
        if not text.strip() or conf_value < 0:
            continue
        
        # Build bbox if available
        bbox = None
        if idx < len(lefts) and idx < len(tops) and idx < len(widths) and idx < len(heights):
            bbox = [
                [lefts[idx], tops[idx]],
                [lefts[idx] + widths[idx], tops[idx]],
                [lefts[idx] + widths[idx], tops[idx] + heights[idx]],
                [lefts[idx], tops[idx] + heights[idx]]
            ]
        
        blocks.append(Block(
            chunk_id=f"{file_id}_img_1_{idx + 1}",
            type="paragraph",
            content=text,
            method="tesseract",
            confidence=conf_value / 100,
            bbox=bbox,
            extracted_at=datetime.now().isoformat()
        ))
    
    return blocks


def compress_image_for_llm(
    file_path: str,
    max_dimension: int = 1024,
    quality: int = 80
) -> str:
    """Compress image and return base64.
    
    Args:
        file_path: Original image path
        max_dimension: Max width or height in pixels
        quality: JPEG quality (70-85)
        
    Returns:
        Base64 encoded compressed image
    """
    with _get_pil().open(file_path) as img:
        # Convert to RGB if needed
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        
        # Resize if needed (maintain aspect ratio)
        if max(img.size) > max_dimension:
            ratio = max_dimension / max(img.size)
            new_size = tuple(int(dim * ratio) for dim in img.size)
            img = img.resize(new_size, _get_pil().Resampling.LANCZOS)
        
        # Compress to JPEG
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode()


def get_image_for_llm(file_path: str, constraints: ImageConstraints = None) -> str:
    """Get image as data URL for sending to LLM.
    
    Args:
        file_path: Path to image
        constraints: Image constraints
        
    Returns:
        Data URL string
    """
    if constraints is None:
        constraints = DEFAULT_IMAGE_CONSTRAINTS
    
    # Validate
    valid, error = validate_image_for_llm(file_path, constraints)
    if not valid:
        raise ValueError(f"Image validation failed: {error}")
    
    # Compress and encode
    b64 = compress_image_for_llm(
        file_path,
        max_dimension=constraints.max_dimension,
        quality=constraints.jpeg_quality
    )
    
    return f"data:image/jpeg;base64,{b64}"
