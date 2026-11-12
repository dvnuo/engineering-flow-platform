"""PDF parser implementation."""

import asyncio
import io
import logging
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Lazy imports for optional dependencies
fitz = None
_paddleocr = None

from .models import Block, ParseResult


logger = logging.getLogger(__name__)

PDF_CONTENT_TYPE = "application/pdf"

# What one PDF contributes to the session's file context. The text is a
# preview: the chat prompt takes an excerpt of it and the agent reads the file
# itself from its workspace path, so a long document is cut here instead of
# being extracted whole. A page costs what its content stream costs (a drawing
# can take seconds and yield a line of text), so the read is also bounded in
# time; the check sits between pages and the first page is always read.
MAX_PDF_PAGES = 100  # leading pages, the cap the parser always had
MAX_PDF_CHARS = 200_000  # across all pages, the same budget a workbook has
MAX_PARSE_SECONDS = 30

# A PDF printed from a browser names a Chinese character by the lowest code
# point its font draws with the same glyph, which for 長, 戶, 行 and some three
# hundred others is a radical (U+2ED1, U+2F3E, U+2F8F): the page looks right
# and its text is not the text. Each radical is read as the ideograph it stands
# for. The Kangxi radicals (U+2F00..2FD5) carry that as their compatibility
# decomposition; the CJK Radicals Supplement is listed here, from Unicode's
# EquivalentUnifiedIdeograph.txt (18.0.0).
_RADICAL_SUPPLEMENT = (
    "2E81:5382 2E82:4E5B 2E83:4E5A 2E84:4E59 2E85:4EBB 2E86:5182 2E87:20628 2E88:5200 "
    "2E89:5202 2E8A:535C 2E8B:353E 2E8C:5C0F 2E8D:5C0F 2E8E:5140 2E8F:5C23 2E90:5C22 "
    "2E91:21BC2 2E92:5DF3 2E93:5E7A 2E94:5F51 2E95:2B739 2E96:5FC4 2E97:5FC3 2E98:624C "
    "2E99:6535 2E9B:65E1 2E9C:65E5 2E9D:6708 2E9E:6B7A 2E9F:6BCD 2EA0:6C11 2EA1:6C35 "
    "2EA2:6C3A 2EA3:706C 2EA4:722B 2EA5:722B 2EA6:4E2C 2EA7:725B 2EA8:72AD 2EA9:738B "
    "2EAA:24D14 2EAB:76EE 2EAC:793A 2EAD:793B 2EAE:25AD7 2EAF:7CF9 2EB0:7E9F 2EB1:7F53 "
    "2EB2:7F52 2EB3:34C1 2EB4:5197 2EB5:2626B 2EB6:7F8A 2EB7:2634C 2EB8:2634B 2EB9:8002 "
    "2EBA:8080 2EBB:807F 2EBC:8089 2EBD:26951 2EBE:8279 2EBF:8279 2EC0:8279 2EC1:864E "
    "2EC2:8864 2EC3:8980 2EC4:897F 2EC5:89C1 2EC6:89D2 2EC7:278B2 2EC8:8BA0 2EC9:8D1D "
    "2ECA:27FB7 2ECB:8F66 2ECC:8FB6 2ECD:8FB6 2ECE:8FB6 2ECF:9091 2ED0:9485 2ED1:9577 "
    "2ED2:9578 2ED3:957F 2ED4:95E8 2ED5:28E0F 2ED6:961D 2ED7:96E8 2ED8:9752 2ED9:97E6 "
    "2EDA:9875 2EDB:98CE 2EDC:98DE 2EDD:98DF 2EDE:2967F 2EDF:98E0 2EE0:9963 2EE1:29810 "
    "2EE2:9A6C 2EE3:9AA8 2EE4:9B3C 2EE5:9C7C 2EE6:9E1F 2EE7:5364 2EE8:9EA6 2EE9:9EC4 "
    "2EEA:9EFE 2EEB:6589 2EEC:9F50 2EED:6B6F 2EEE:9F7F 2EEF:7ADC 2EF0:9F99 2EF1:9F9C "
    "2EF2:4E80 2EF3:9F9F"
)
_RADICAL_TO_IDEOGRAPH: Dict[int, int] = {
    int(pair[:4], 16): int(pair[5:], 16) for pair in _RADICAL_SUPPLEMENT.split()
}
_RADICAL_TO_IDEOGRAPH.update(
    (code, ord(unicodedata.normalize("NFKC", chr(code)))) for code in range(0x2F00, 0x2FD6)
)


def _get_pypdf():
    """Lazy load pypdf, the text extractor the runtime image ships (BSD)."""
    try:
        import pypdf
    except ImportError:
        return None
    # A damaged file makes pypdf warn once per broken object; what matters is
    # in the parse result.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    return pypdf


def _get_fitz():
    """Lazy load PyMuPDF/fitz.

    PyMuPDF is AGPL and is not in requirements.txt: it is used only where
    someone installed it, as a second text extractor and to render the pages
    of a scanned PDF for OCR.
    """
    global fitz
    if fitz is None:
        try:
            import fitz
        except ImportError:
            try:
                import pymupdf as fitz
            except ImportError:
                pass
    return fitz


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


async def parse_pdf(file_path: str, options: Dict = None) -> ParseResult:
    """Parse PDF file.

    Strategy:
    1. Text: pypdf, one block per page. PyMuPDF is tried after it when it is
       installed, and pdfplumber adds the tables when it is installed.
    2. No text layer (a scanned PDF): OCR of the rendered pages, which needs
       PyMuPDF; without it the parse fails and says why.

    The file is read on a worker thread: pypdf is pure Python and a long
    document takes seconds, and the gateway's event loop also answers the
    readiness probe.

    Args:
        file_path: Path to PDF file
        options: Parser options (``max_pages``)

    Returns:
        ParseResult
    """
    return await asyncio.to_thread(_parse_pdf_sync, file_path, options or {})


def _parse_pdf_sync(file_path: str, options: Dict) -> ParseResult:
    start_time = time.time()
    max_pages = _positive_int(options.get("max_pages"), MAX_PDF_PAGES)
    deadline = time.monotonic() + MAX_PARSE_SECONDS

    file_id = Path(file_path).stem.split("_")[0]
    filename = Path(file_path).name

    def _failure(error: str) -> ParseResult:
        return ParseResult(
            success=False,
            content_type=PDF_CONTENT_TYPE,
            file_id=file_id,
            filename=filename,
            error=error,
            parse_time_ms=int((time.time() - start_time) * 1000),
        )

    readers: List[Callable[..., Dict[str, Any]]] = []
    if _get_pypdf() is not None:
        readers.append(_read_with_pypdf)
    if _get_fitz() is not None:
        readers.extend([_read_with_pymupdf, _read_with_ocr])
    if not readers:
        return _failure("pypdf not installed")

    # The first reader that finds text wins; one that finds none still says
    # how many pages the document has.
    read: Optional[Dict[str, Any]] = None
    open_error: Optional[str] = None
    for reader in readers:
        try:
            attempt = reader(file_path, max_pages=max_pages, deadline=deadline)
        except Exception as exc:
            if read is None and open_error is None:
                open_error = _error_text(exc)
            continue
        if read is None or attempt["pages"]:
            read = attempt
        if attempt["pages"]:
            break

    if read is None or not read["pages"]:
        # Why the first reader could not open the file says more than a later
        # one that opened it and found nothing (a password-protected file, for one).
        if open_error:
            return _failure(open_error)
        if read["page_errors"]:
            number, error = next(iter(read["page_errors"].items()))
            return _failure(f"Page {number} could not be read: {error}")
        return _failure("No text could be extracted from this PDF: its pages have no text layer (a scanned document?)")

    tables = _read_tables_with_pdfplumber(
        file_path,
        pages_read=read["pages_read"],
        max_chars=MAX_PDF_CHARS - read["chars"],
        deadline=deadline,
    )

    now = datetime.now().isoformat()
    confidence = 0.8 if read["method"] == "ocr" else 0.95
    blocks: List[Block] = []
    note = _excerpt_note(read)
    if note:
        # First, so it is still there when the prompt keeps only the head of the text.
        blocks.append(Block(
            chunk_id=f"{file_id}_pdf_excerpt_note",
            type="paragraph",
            content=note,
            method=read["method"],
            confidence=confidence,
            extracted_at=now,
        ))
    texts = dict(read["pages"])
    for number in sorted(set(texts) | set(tables)):
        if number in texts:
            blocks.append(Block(
                chunk_id=f"{file_id}_pdf_{number}_1",
                type="paragraph",
                content=texts[number],
                page=number,
                method=read["method"],
                confidence=confidence,
                extracted_at=now,
            ))
        for table_idx, table in enumerate(tables.get(number, []), 1):
            blocks.append(Block(
                chunk_id=f"{file_id}_pdf_{number}_table_{table_idx}",
                type="table",
                content="",
                markdown=_table_to_markdown(table),
                table_json=_table_to_json(table),
                page=number,
                method="pdfplumber",
                confidence=0.9,
                extracted_at=now,
            ))

    return ParseResult(
        success=True,
        content_type=PDF_CONTENT_TYPE,
        file_id=file_id,
        filename=filename,
        markdown=_blocks_to_markdown(blocks),
        blocks=blocks,
        json={
            "pdf_type": "scanned" if read["method"] == "ocr" else "text",
            "pages": read["total_pages"],
            "pages_read": read["pages_read"],
            "pages_with_text": len(read["pages"]),
            "truncated": read["truncated"],
            "tables": sum(len(found) for found in tables.values()),
        },
        parse_time_ms=int((time.time() - start_time) * 1000),
    )


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _error_text(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


def _clean_page_text(text: Any) -> str:
    """A page's text as it is stored.

    No NULs, no unpaired surrogates, no runs of blank lines, and the ideograph
    where the PDF named a radical.
    """
    if not isinstance(text, str):
        return ""
    # A font with a broken character map yields NULs and unpaired surrogates,
    # and neither survives the UTF-8 the file context is stored in.
    text = text.replace("\x00", "")
    text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    text = text.translate(_RADICAL_TO_IDEOGRAPH)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _read_pages(
    total_pages: int,
    page_text: Callable[[int], str],
    *,
    max_pages: int,
    deadline: float,
    method: str,
) -> Dict[str, Any]:
    """Collect the text of the leading pages, within the page, character and time limits.

    ``page_text`` takes a 0-based page index. Returns the pages that have text
    as ``(page number, text)``, the pages that have none, the pages that could
    not be read, how many pages were looked at, whether the last page's text
    was cut at the character limit, and whether anything was left out. A page
    that raises is skipped: one damaged page does not cost the rest of the
    document.
    """
    pages: List[Tuple[int, str]] = []
    empty_pages: List[int] = []
    page_errors: Dict[int, str] = {}
    pages_read = 0
    chars = 0
    cut = False
    for number in range(1, total_pages + 1):
        if number > max_pages or chars >= MAX_PDF_CHARS or (pages_read and time.monotonic() > deadline):
            break
        pages_read = number
        try:
            text = _clean_page_text(page_text(number - 1))
        except Exception as exc:
            page_errors[number] = _error_text(exc)
            continue
        if not text:
            empty_pages.append(number)
            continue
        if chars + len(text) > MAX_PDF_CHARS:
            text = text[: MAX_PDF_CHARS - chars].rstrip()
            cut = True
        pages.append((number, text))
        chars += len(text)
    return {
        "method": method,
        "total_pages": total_pages,
        "pages_read": pages_read,
        "pages": pages,
        "empty_pages": empty_pages,
        "page_errors": page_errors,
        "chars": chars,
        "cut": cut,
        "truncated": cut or pages_read < total_pages,
    }


def _read_with_pypdf(file_path: str, *, max_pages: int, deadline: float) -> Dict[str, Any]:
    """Extract the text layer with pypdf."""
    pypdf = _get_pypdf()
    source = None
    reader = None
    try:
        # The file is opened here rather than by pypdf: given a path pypdf
        # copies the whole file into memory, and this handle is released
        # whatever the reader makes of a damaged file.
        source = open(file_path, "rb")
        reader = pypdf.PdfReader(source)
        try:
            # An empty user password (a file that only restricts printing or
            # copying) is tried by pypdf itself.
            total_pages = len(reader.pages)
        except pypdf.errors.FileNotDecryptedError:
            raise ValueError("The PDF is password-protected and cannot be read without its password") from None
        return _read_pages(
            total_pages,
            lambda index: reader.pages[index].extract_text(),
            max_pages=max_pages,
            deadline=deadline,
            method="pypdf",
        )
    finally:
        for handle in (reader, source):
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass


def _read_with_pymupdf(file_path: str, *, max_pages: int, deadline: float) -> Dict[str, Any]:
    """Extract the text layer with PyMuPDF, where it is installed."""
    with _get_fitz().open(file_path) as doc:
        return _read_pages(
            doc.page_count,
            lambda index: doc[index].get_text("text"),
            max_pages=max_pages,
            deadline=deadline,
            method="pymupdf",
        )


def _read_with_ocr(file_path: str, *, max_pages: int, deadline: float) -> Dict[str, Any]:
    """OCR the pages of a PDF that has no text layer; PyMuPDF renders them."""
    fitz_module = _get_fitz()
    with fitz_module.open(file_path) as doc:
        def _page_text(index: int) -> str:
            pix = doc[index].get_pixmap(matrix=fitz_module.Matrix(2, 2))  # 2x scale
            return _ocr_page_image(pix.tobytes("png"))

        return _read_pages(doc.page_count, _page_text, max_pages=max_pages, deadline=deadline, method="ocr")


def _ocr_page_image(png: bytes) -> str:
    """The text of one rendered page: PaddleOCR when it is installed, else Tesseract."""
    ocr = _get_paddleocr()
    if ocr is not None:
        # PaddleOCR needs a file path
        tmp_path = None
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(png)
                tmp_path = tmp.name

            results = ocr.ocr(tmp_path, cls=True)
            lines = [str(line[1][0]).strip() for line in (results[0] if results and results[0] else [])]
            text = "\n".join(line for line in lines if line)
            if text:
                return text
        except Exception:
            pass
        finally:
            if tmp_path:
                try:
                    import os
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # Fallback to Tesseract
    try:
        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(png)) as img:
            return pytesseract.image_to_string(img)
    except Exception:
        return ""


def _read_tables_with_pdfplumber(
    file_path: str, *, pages_read: int, max_chars: int, deadline: float
) -> Dict[int, List[List[List[Any]]]]:
    """The tables of the pages that were read, by page number; nothing without pdfplumber.

    The tables repeat text the page already has, so they take what is left of
    the document's character budget and stop with it.
    """
    try:
        import pdfplumber
    except ImportError:
        return {}

    tables: Dict[int, List[List[List[Any]]]] = {}
    budget = max_chars
    try:
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages[:pages_read], 1):
                if budget <= 0 or time.monotonic() > deadline:
                    break
                for table in page.extract_tables():
                    if not table:
                        continue
                    tables.setdefault(page_num, []).append(table)
                    budget -= sum(len(str(cell)) + 3 for row in table for cell in row if cell is not None)
    except Exception as e:
        logger.warning("PDF table extraction failed: %s", e)

    return tables


def _page_ranges(numbers: List[int]) -> str:
    """Page numbers as ``3, 7-9``."""
    ranges: List[List[int]] = []
    for number in sorted(numbers):
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return ", ".join(str(first) if first == last else f"{first}-{last}" for first, last in ranges)


def _excerpt_note(read: Dict[str, Any]) -> str:
    """What of the document the text does not hold; empty when it holds all of it."""
    total_pages = read["total_pages"]
    parts: List[str] = []
    if read["truncated"]:
        partway = ", partway through the page" if read["cut"] else ""
        parts.append(
            f"The text stops at page {read['pages_read']} of {total_pages}{partway}: the document is longer "
            "than the excerpt, and the rest of it is not here."
        )
    if read["empty_pages"]:
        why = "" if read["method"] == "ocr" else " (scanned images are not read)"
        parts.append(f"Pages with no text{why}: {_page_ranges(read['empty_pages'])}.")
    if read["page_errors"]:
        parts.append(f"Pages that could not be read: {_page_ranges(list(read['page_errors']))}.")
    if not parts:
        return ""
    return f"Note on this {total_pages}-page PDF: " + " ".join(parts)


def _table_to_markdown(table: List[List[str]]) -> str:
    """Convert table to markdown format.

    Args:
        table: 2D list of cell values

    Returns:
        Markdown table string
    """
    if not table:
        return ""

    # Handle empty table
    if not table or not table[0]:
        return ""

    max_cols = max(len(row) for row in table)
    col_widths = [max(len(str(row[i])) if i < len(row) else 0 for row in table) for i in range(max_cols)]

    lines = []

    # Header (use first row)
    header_row = table[0]
    header = "| " + " | ".join(
        str(header_row[i]).ljust(col_widths[i]) if i < len(header_row) else " " * col_widths[i]
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
    return [["" if cell is None else str(cell) for cell in row] for row in table]


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
