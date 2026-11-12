"""Excel and CSV parser implementation."""

import asyncio
import time
import io
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Block, ParseResult

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# What one workbook contributes to the session's file context. The text is a
# preview: the chat prompt takes an excerpt of it and the agent reads the file
# itself from its workspace path, so a large workbook is cut here instead of
# being rendered whole. The workbook is streamed (read-only), which keeps the
# memory flat whatever the file size.
MAX_SHEET_ROWS = 10000  # per sheet, the same cap csv has
MAX_SHEET_COLUMNS = 200  # a sheet can declare 16384 columns and use five
MAX_WORKBOOK_CHARS = 200_000  # across all sheets


async def parse_excel(file_path: str, options: Dict = None) -> ParseResult:
    """Parse Excel file (XLSX).

    The workbook is read on a worker thread: a large one takes seconds, and
    the gateway's event loop also answers the readiness probe.

    Args:
        file_path: Path to XLSX file
        options: Parser options (``max_rows`` per sheet)

    Returns:
        ParseResult
    """
    return await asyncio.to_thread(_parse_excel_sync, file_path, options or {})


def _parse_excel_sync(file_path: str, options: Dict) -> ParseResult:
    start_time = time.time()
    max_rows = _positive_int(options.get("max_rows"), MAX_SHEET_ROWS)

    file_id = Path(file_path).stem.split("_")[0]
    filename = Path(file_path).name

    def _failure(error: str) -> ParseResult:
        return ParseResult(
            success=False,
            content_type=XLSX_CONTENT_TYPE,
            file_id=file_id,
            filename=filename,
            error=error,
            parse_time_ms=int((time.time() - start_time) * 1000),
        )

    try:
        import openpyxl
    except ImportError:
        return _failure("openpyxl not installed")

    source = None
    wb = None
    try:
        # The file is opened here rather than by openpyxl, so the handle is
        # released even when the workbook cannot be loaded. read_only streams
        # each sheet instead of building every cell in memory; keep_links=False
        # skips the cached copies of other workbooks.
        source = open(file_path, "rb")
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True, keep_links=False)

        blocks: List[Block] = []
        sheet_names: List[str] = []
        sheets_not_read: List[str] = []
        sheet_errors: Dict[str, str] = {}
        budget = MAX_WORKBOOK_CHARS

        # Chart sheets are in wb.sheetnames but hold no cells.
        for sheet_idx, sheet in enumerate(wb.worksheets):
            sheet_name = str(sheet.title)
            sheet_names.append(sheet_name)
            if budget <= 0:
                sheets_not_read.append(sheet_name)
                continue
            try:
                read = _read_sheet_rows(sheet, max_rows=max_rows, max_chars=budget)
            except Exception as exc:
                sheet_errors[sheet_name] = str(exc) or type(exc).__name__
                continue
            budget -= read["chars"]
            rows = read["rows"]
            if not rows:
                continue

            table_block = _rows_to_table_block(rows, sheet_idx + 1, sheet_name, file_id)
            caption = _sheet_caption(sheet_name, read)
            table_block.content = f"{caption}\n\n{table_block.markdown}"
            table_block.markdown = table_block.content
            table_block.row_range = f"1-{read['last_row']}"
            blocks.append(table_block)

            # Also add as paragraphs for small sheets
            if len(rows) <= 10 and not read["truncated"]:
                for row_number, row in zip(read["row_numbers"], rows):
                    text = " | ".join(row)
                    blocks.append(Block(
                        chunk_id=f"{file_id}_xlsx_{sheet_idx + 1}_{row_number}",
                        type="paragraph",
                        content=text,
                        sheet=sheet_name,
                        row_range=f"{row_number}-{row_number}",
                        method="openpyxl",
                        confidence=0.95,
                        extracted_at=datetime.now().isoformat()
                    ))

        if not blocks and sheet_errors:
            name, error = next(iter(sheet_errors.items()))
            return _failure(f'Sheet "{name}" could not be read: {error}')

        if blocks and sheets_not_read:
            blocks.append(Block(
                chunk_id=f"{file_id}_xlsx_sheets_not_read",
                type="paragraph",
                content=(
                    "Sheets of this workbook that are not in this excerpt (the workbook is larger than "
                    "the excerpt): " + ", ".join(f'"{name}"' for name in sheets_not_read)
                ),
                method="openpyxl",
                confidence=0.95,
                extracted_at=datetime.now().isoformat()
            ))

        # Generate markdown
        markdown = _blocks_to_markdown(blocks)

        return ParseResult(
            success=True,
            content_type=XLSX_CONTENT_TYPE,
            file_id=file_id,
            filename=filename,
            markdown=markdown,
            blocks=blocks,
            json={
                "sheets": len(sheet_names),
                "sheet_names": sheet_names,
                "sheets_not_read": sheets_not_read,
                "sheet_errors": sheet_errors,
            },
            parse_time_ms=int((time.time() - start_time) * 1000)
        )

    except Exception as e:
        return _failure(str(e) or type(e).__name__)
    finally:
        for handle in (wb, source):
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _declared_rows(sheet: Any) -> Optional[int]:
    """The row count the sheet's own dimension record states, when it has one."""
    try:
        declared = sheet.max_row
    except Exception:
        return None
    return declared if isinstance(declared, int) and declared > 0 else None


def _read_sheet_rows(sheet: Any, *, max_rows: int, max_chars: int) -> Dict[str, Any]:
    """Stream a sheet's leading rows as text, within a row, column and character limit.

    Returns the rows (blank rows dropped, trailing blank cells trimmed) with
    their sheet row numbers, the last sheet row the table covers, the
    characters used, and whether rows or columns were left out. One column past
    the limit is read so a sheet that really is wider is told apart from one
    that only declares a wide range.
    """
    declared_rows = _declared_rows(sheet)
    # The dimension record is a hint some writers get wrong (a streamed export
    # can leave "A1" there), and a read-only sheet stops reading at it. It is
    # dropped, and the limits here bound the read instead.
    sheet.reset_dimensions()

    rows: List[List[str]] = []
    row_numbers: List[int] = []
    scanned = 0
    chars = 0
    truncated = False
    columns_cut = False
    for values in sheet.iter_rows(max_col=MAX_SHEET_COLUMNS + 1, values_only=True):
        if scanned >= max_rows or chars >= max_chars:
            truncated = True
            break
        scanned += 1
        if len(values) > MAX_SHEET_COLUMNS:
            if values[MAX_SHEET_COLUMNS] is not None:
                columns_cut = True
            values = values[:MAX_SHEET_COLUMNS]
        row = [str(cell) if cell is not None else "" for cell in values]
        while row and not row[-1].strip():
            row.pop()
        if not row:
            continue
        rows.append(row)
        row_numbers.append(scanned)
        # Cell text plus the " | " that joins the cells of a row.
        chars += sum(len(cell) for cell in row) + 3 * len(row)
    return {
        "rows": rows,
        "row_numbers": row_numbers,
        # A cut read covers every row scanned; a complete one ends at its last row with a value.
        "last_row": scanned if truncated else (row_numbers[-1] if row_numbers else 0),
        "declared_rows": declared_rows,
        "chars": chars,
        "truncated": truncated,
        "columns_cut": columns_cut,
    }


def _sheet_caption(sheet_name: str, read: Dict[str, Any]) -> str:
    """The line above a sheet's table: its name and what of the sheet the table holds."""
    last_row = read["last_row"]
    if read["truncated"]:
        declared = read.get("declared_rows")
        total = f" of {declared}" if declared and declared > last_row else ""
        caption = f'Sheet "{sheet_name}": rows 1-{last_row}{total} only, the sheet has more rows'
    else:
        caption = f'Sheet "{sheet_name}": rows 1-{last_row}'
    if read["columns_cut"]:
        caption += f"; columns after the first {MAX_SHEET_COLUMNS} are left out"
    return caption + "."


async def parse_csv(file_path: str, options: Dict = None) -> ParseResult:
    """Parse CSV file.
    
    Args:
        file_path: Path to CSV file
        options: Parser options
        
    Returns:
        ParseResult
    """
    start_time = time.time()
    options = options or {}
    max_rows = options.get("max_rows", 10000)
    
    file_id = Path(file_path).stem.split("_")[0]
    filename = Path(file_path).name
    
    try:
        import pandas as pd
    except ImportError:
        # Fallback to basic csv
        return await _parse_csv_basic(file_path, options, file_id, filename, start_time)
    
    try:
        # Read CSV with pandas
        df = pd.read_csv(file_path, nrows=max_rows)
        
        # Convert to rows
        rows = [df.columns.tolist()] + df.values.tolist()
        rows = [[str(cell) for cell in row] for row in rows]
        
        # Create table block
        table_block = _rows_to_table_block(rows, 1, "Sheet1", file_id, "csv", "pandas")
        
        # Also add as paragraphs
        blocks = [table_block]
        
        # Add header info
        blocks.append(Block(
            chunk_id=f"{file_id}_csv_columns_1",
            type="paragraph",
            content=f"Columns: {', '.join(df.columns)}",
            row_range=f"1-{len(df)}",
            method="pandas",
            confidence=0.95,
            extracted_at=datetime.now().isoformat()
        ))
        
        # Add sample rows
        for idx, row in df.head(5).iterrows():
            text = " | ".join(str(v) for v in row.values)
            blocks.append(Block(
                chunk_id=f"{file_id}_csv_{idx + 2}",
                type="paragraph",
                content=text,
                row_range=f"{idx + 2}-{idx + 2}",
                method="pandas",
                confidence=0.95,
                extracted_at=datetime.now().isoformat()
            ))
        
        markdown = _blocks_to_markdown(blocks)
        
        return ParseResult(
            success=True,
            content_type="text/csv",
            file_id=file_id,
            filename=filename,
            markdown=markdown,
            blocks=blocks,
            json={
                "rows": len(df),
                "columns": len(df.columns),
                "column_names": df.columns.tolist()
            },
            parse_time_ms=int((time.time() - start_time) * 1000)
        )
        
    except Exception as e:
        return await _parse_csv_basic(file_path, options, file_id, filename, start_time)


async def _parse_csv_basic(file_path: str, options: Dict, file_id: str, filename: str, start_time: float) -> ParseResult:
    """Basic CSV parsing without pandas."""
    import csv
    
    max_rows = options.get("max_rows", 10000)
    
    try:
        rows = []
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for idx, row in enumerate(reader):
                if idx >= max_rows:
                    break
                rows.append(row)
        
        if not rows:
            return ParseResult(
                success=False,
                content_type="text/csv",
                file_id=file_id,
                filename=filename,
                error="Empty CSV"
            )
        
        table_block = _rows_to_table_block(rows, 1, "Sheet1", file_id, "csv", "python-csv")
        
        markdown = _blocks_to_markdown([table_block])
        
        return ParseResult(
            success=True,
            content_type="text/csv",
            file_id=file_id,
            filename=filename,
            markdown=markdown,
            blocks=[table_block],
            json={"rows": len(rows)},
            parse_time_ms=int((time.time() - start_time) * 1000)
        )
        
    except Exception as e:
        return ParseResult(
            success=False,
            content_type="text/csv",
            file_id=file_id,
            filename=filename,
            error=str(e),
            parse_time_ms=int((time.time() - start_time) * 1000)
        )


def _rows_to_table_block(rows: List[List[str]], sheet_idx: int, sheet_name: str, file_id: str, source_type: str = "xlsx", method: str = "openpyxl") -> Block:
    """Convert rows to a table block."""
    if not rows:
        return Block(
            chunk_id=f"{file_id}_{source_type}_{sheet_idx}_1",
            type="table",
            content="",
            sheet=sheet_name,
            method=method,
            confidence=0.95,
            extracted_at=datetime.now().isoformat()
        )
    
    markdown = _table_to_markdown(rows)
    json_table = _table_to_json(rows)
    row_range = f"1-{len(rows)}"
    
    return Block(
        chunk_id=f"{file_id}_{source_type}_{sheet_idx}_1",
        type="table",
        content=markdown,
        markdown=markdown,
        table_json=json_table,
        sheet=sheet_name,
        row_range=row_range,
        method=method,
        confidence=0.95,
        extracted_at=datetime.now().isoformat()
    )


def _markdown_cell(value: Any) -> str:
    """A cell on one table row: line breaks become spaces and a pipe is escaped."""
    text = str(value)
    if "\n" in text or "\r" in text:
        text = " ".join(text.split())
    return text.replace("|", "\\|")


def _table_to_markdown(rows: List[List[str]]) -> str:
    """Convert rows to markdown table.

    Cells are not padded to a column width: one long cell would otherwise
    widen every row of its column.
    """
    if not rows:
        return ""

    # Handle unequal row lengths
    max_cols = max(len(row) for row in rows)

    def _line(row: List[str]) -> str:
        cells = [_markdown_cell(cell) for cell in row] + [""] * (max_cols - len(row))
        return "| " + " | ".join(cells) + " |"

    lines = [_line(rows[0]), "| " + " | ".join("---" for _ in range(max_cols)) + " |"]
    lines.extend(_line(row) for row in rows[1:])
    return "\n".join(lines)


def _table_to_json(rows: List[List[str]]) -> List[List[str]]:
    """Convert rows to JSON."""
    return [[str(cell) for cell in row] for row in rows]


def _blocks_to_markdown(blocks: List[Block]) -> str:
    """Convert blocks to markdown."""
    md_parts = []
    
    for block in blocks:
        if block.type == "heading":
            level = block.level or 1
            md_parts.append(f"{'#' * level} {block.content}\n")
        elif block.type == "paragraph":
            md_parts.append(f"{block.content}\n")
        elif block.type == "table" and block.markdown:
            md_parts.append(f"\n{block.markdown}\n")
    
    return "\n".join(md_parts)
