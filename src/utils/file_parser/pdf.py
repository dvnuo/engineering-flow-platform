"""PDF parser implementation."""

import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .models import Block, ParseResult
from .validators import get_mime_type


async def parse_pdf(file_path: str, options: Dict = None) -> ParseResult:
    """Parse PDF file.
    
    Strategy:
    1. Detect PDF type (text/scanned)
    2. Text: PyMuPDF + pdfplumber for tables
    3. Scanned: OCR + Vision LLM fallback
    
    Args:
        file_path: Path to PDF file
        options: Parser options
        
    Returns:
        ParseResult
    """
    start_time = time.time()
    options = options or {}
    
    file_id = Path(file_path).stem.split("_")[0]
    filename = Path(file_path).name
    
    try:
        # Detect PDF type
        pdf_type = detect_pdf_type(file_path)
        
        blocks = []
        errors = []
        
        # Process based on type
        if pdf_type in ("text", "mixed"):
            try:
                text_blocks = await extract_text_with_pymupdf(file_path, options)
                blocks.extend(text_blocks)
                
                # Extract tables
                table_blocks = await extract_tables_with_pdfplumber(file_path, options)
                blocks.extend(table_blocks)
            except Exception as e:
                errors.append(f"Text extraction failed: {e}")
        
        # If no content or scanned, try OCR
        if pdf_type == "scanned" or not blocks:
            try:
                ocr_blocks = await extract_with_ocr(file_path, options)
                blocks.extend(ocr_blocks)
            except Exception as e:
                errors.append(f"OCR failed: {e}")
        
        # Sort by page
        blocks.sort(key=lambda b: b.page or 0)
        
        # Generate markdown
        markdown = _blocks_to_markdown(blocks)
        
        return ParseResult(
            success=len(blocks) > 0,
            content_type="application/pdf",
            file_id=file_id,
            filename=filename,
            markdown=markdown,
            blocks=blocks,
            json={
                "pdf_type": pdf_type,
                "pages": len(set(b.page for b in blocks if b.page)),
                "tables": len([b for b in blocks if b.type == "table"])
            },
            parse_time_ms=int((time.time() - start_time) * 1000),
            error="; ".join(errors) if errors else None
        )
        
    except Exception as e:
        return ParseResult(
            success=False,
            content_type="application/pdf",
            file_id=file_id,
            filename=filename,
            error=str(e),
            parse_time_ms=int((time.time() - start_time) * 1000)
        )


def detect_pdf_type(file_path: str) -> str:
    """Detect if PDF is text-rich or scanned.
    
    Args:
        file_path: Path to PDF
        
    Returns:
        "text", "scanned", or "mixed"
    """
    try:
        import PyMuPDF as fitz
    except ImportError:
        return "scanned"  # Default to OCR if no PyMuPDF
    
    try:
        with fitz.open(file_path) as doc:
            text_pages = 0
            image_pages = 0
            
            for page in doc:
                text = page.get_text()
                images = page.get_images()
                
                if text.strip():
                    text_pages += 1
                if images:
                    image_pages += 1
            
            if text_pages == 0 and image_pages > 0:
                return "scanned"
            elif image_pages > 0 and text_pages > 0:
                return "mixed"
            else:
                return "text"
    except Exception:
        return "scanned"


async def extract_text_with_pymupdf(file_path: str, options: Dict) -> List[Block]:
    """Extract text using PyMuPDF.
    
    Args:
        file_path: Path to PDF
        options: Options including max_pages
        
    Returns:
        List of text blocks
    """
    try:
        import PyMuPDF as fitz
    except ImportError:
        return []
    
    max_pages = options.get("max_pages", 100)
    blocks = []
    
    try:
        with fitz.open(file_path) as doc:
            for page_num, page in enumerate(doc):
                if page_num + 1 > max_pages:
                    break
                
                text = page.get_text("text")
                if not text.strip():
                    continue
                
                # Split by paragraphs
                paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
                
                for para_idx, para in enumerate(paragraphs):
                    if not para:
                        continue
                    
                    # Determine if it's a heading
                    level = None
                    if para.startswith("#"):
                        # Markdown heading
                        pass
                    elif len(para) < 100 and para.isupper():
                        level = 1
                    elif len(para) < 50 and not("\n" in para):
                        level = 2
                    
                    block_type = "heading" if level else "paragraph"
                    
                    blocks.append(Block(
                        chunk_id=f"pdf_{page_num + 1}_{para_idx + 1}",
                        type=block_type,
                        content=para,
                        level=level,
                        page=page_num + 1,
                        method="pymupdf",
                        confidence=0.95,
                        extracted_at=datetime.now().isoformat()
                    ))
    
    except Exception as e:
        pass  # Return empty list on error
    
    return blocks


async def extract_tables_with_pdfplumber(file_path: str, options: Dict) -> List[Block]:
    """Extract tables using pdfplumber.
    
    Args:
        file_path: Path to PDF
        options: Options
        
    Returns:
        List of table blocks
    """
    try:
        import pdfplumber
    except ImportError:
        return []
    
    blocks = []
    
    try:
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                
                for table_idx, table in enumerate(tables):
                    if not table:
                        continue
                    
                    # Convert to markdown and JSON
                    markdown_table = _table_to_markdown(table)
                    json_table = _table_to_json(table)
                    
                    blocks.append(Block(
                        chunk_id=f"pdf_{page_num + 1}_table_{table_idx + 1}",
                        type="table",
                        content="",
                        markdown=markdown_table,
                        table_json=json_table,
                        page=page_num + 1,
                        method="pdfplumber",
                        confidence=0.9,
                        extracted_at=datetime.now().isoformat()
                    ))
    
    except Exception:
        pass  # Return empty on error
    
    return blocks


async def extract_with_ocr(file_path: str, options: Dict) -> List[Block]:
    """Extract text using OCR.
    
    Args:
        file_path: Path to PDF
        options: Options
        
    Returns:
        List of text blocks
    """
    # Convert PDF pages to images first
    try:
        import PyMuPDF as fitz
    except ImportError:
        return []
    
    blocks = []
    
    try:
        # Open PDF and convert each page to image
        with fitz.open(file_path) as doc:
            for page_num, page in enumerate(doc):
                # Render page to image
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x scale
                
                # Save to temporary buffer
                import io
                img_buffer = io.BytesIO(pix.tobytes("png"))
                
                # OCR the image
                page_blocks = await _ocr_image_buffer(img_buffer, page_num + 1)
                blocks.extend(page_blocks)
    
    except Exception:
        pass
    
    return blocks


async def _ocr_image_buffer(buffer: io.BytesIO, page_num: int) -> List[Block]:
    """OCR an image buffer.
    
    Args:
        buffer: BytesIO containing PNG image
        page_num: Page number
        
    Returns:
        List of text blocks
    """
    # Try PaddleOCR first
    try:
        from paddleocr import PaddleOCR
        import tempfile
        
        ocr = PaddleOCR(use_angle_cls=True, lang='ch_en', show_log=False)
        
        # Save to temp file (PaddleOCR needs file path)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(buffer.getvalue())
            tmp_path = tmp.name
        
        try:
            results = ocr.ocr(tmp_path, cls=True)
            
            if not results or not results[0]:
                return []
            
            blocks = []
            for line_idx, line in enumerate(results[0]):
                box, (text, confidence) = line
                if not text.strip():
                    continue
                
                blocks.append(Block(
                    chunk_id=f"pdf_{page_num}_ocr_{line_idx + 1}",
                    type="paragraph",
                    content=text,
                    page=page_num,
                    method="paddleocr",
                    confidence=confidence,
                    bbox=box,
                    extracted_at=datetime.now().isoformat()
                ))
            
            return blocks
        finally:
            import os
            os.unlink(tmp_path)
    
    except Exception:
        pass
    
    # Fallback to Tesseract
    try:
        import pytesseract
        from PIL import Image
        import io
        
        buffer.seek(0)
        img = Image.open(buffer)
        text = pytesseract.image_to_string(img)
        
        if not text.strip():
            return []
        
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        
        return [
            Block(
                chunk_id=f"pdf_{page_num}_ocr_{idx + 1}",
                type="paragraph",
                content=line,
                page=page_num,
                method="tesseract",
                confidence=0.8,
                extracted_at=datetime.now().isoformat()
            )
            for idx, line in enumerate(lines)
        ]
    
    except Exception:
        return []


def _table_to_markdown(table: List[List[str]]) -> str:
    """Convert table to markdown format.
    
    Args:
        table: 2D list of cell values
        
    Returns:
        Markdown table string
    """
    if not table:
        return ""
    
    # Get column widths
    col_widths = [max(len(str(row[i])) if i < len(row) else 0 for row in table) for i in range(len(table[0]))]
    
    lines = []
    
    # Header
    header = "| " + " | ".join(
        str(row[i]).ljust(col_widths[i]) if i < len(row) else " " * col_widths[i]
        for i in range(len(col_widths))
    ) + " |"
    lines.append(header)
    
    # Separator
    sep = "| " + " | ".join("-" * w for w in col_widths) + " |"
    lines.append(sep)
    
    # Data rows
    for row in table[1:]:
        data = "| " + " | ".join(
            str(row[i]).ljust(col_widths[i]) if i < len(row) else " " * col_widths[i]
            for i in range(len(col_widths))
        ) + " |"
        lines.append(data)
    
    return "\n".join(lines)


def _table_to_json(table: List[List[str]]) -> List[List[str]]:
    """Convert table to JSON format.
    
    Args:
        table: 2D list of cell values
        
    Returns:
        2D list suitable for JSON
    """
    return [[str(cell) if cell else "" for cell in row] for row in table]


def _blocks_to_markdown(blocks: List[Block]) -> str:
    """Convert blocks to markdown.
    
    Args:
        blocks: List of blocks
        
    Returns:
        Markdown string
    """
    md_parts = []
    current_page = None
    
    for block in blocks:
        # Add page separator
        if block.page and block.page != current_page:
            md_parts.append(f"\n--- Page {block.page} ---\n")
            current_page = block.page
        
        if block.type == "heading":
            level = block.level or 1
            md_parts.append(f"{'#' * level} {block.content}\n")
        elif block.type == "paragraph":
            md_parts.append(f"{block.content}\n")
        elif block.type == "table" and block.markdown:
            md_parts.append(f"\n{block.markdown}\n")
        elif block.type == "list":
            md_parts.append(f"- {block.content}\n")
    
    return "\n".join(md_parts)
