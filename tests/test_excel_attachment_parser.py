"""The xlsx attachment parser.

A member attached a 28 MB workbook and the chat answered 400: the runtime image
never shipped openpyxl, so the parser said "openpyxl not installed", and with
the library present it would have built every cell of the workbook in memory on
the gateway's event loop.

- requirements.txt ships the libraries the xlsx and docx parsers import.
- The workbook is streamed on a worker thread and its handle is released.
- What a workbook contributes is bounded in rows, columns and characters; the
  table says what of the sheet it holds and which sheets were left out.
- The sheet's own dimension record is not trusted.
"""
import asyncio
import io
import threading
import zipfile
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from src.utils.file_parser import excel  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def _save(workbook, path: Path) -> Path:
    workbook.save(path)
    workbook.close()
    return path


def _with_sheet_xml(path: Path, edit) -> None:
    """Rewrite each worksheet part of a saved workbook through ``edit(bytes) -> bytes``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.startswith("xl/worksheets/sheet"):
                data = edit(data)
            target.writestr(item, data)
    path.write_bytes(buffer.getvalue())


def test_requirements_ship_the_attachment_parser_libraries():
    """The image installs requirements.txt only: a parser whose library is not listed cannot run there."""
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    packages = {line.split(">")[0].split("=")[0].split("<")[0].strip() for line in requirements.splitlines()}
    assert "openpyxl" in packages
    assert "python-docx" in packages


@pytest.mark.asyncio
async def test_parse_excel_renders_each_sheet_with_its_name_and_rows(tmp_path):
    workbook = openpyxl.Workbook()
    tasks = workbook.active
    tasks.title = "Tasks"
    tasks.append(["Task", "Owner", "Note"])
    tasks.append(["Build", "Ann", "line one\nline two | done"])
    tasks.append([None, None, None])
    tasks.append(["Ship", "Bo", 3.5])
    workbook.create_sheet("Empty")
    totals = workbook.create_sheet("Totals")
    totals.append(["Region", "Total"])
    totals.append(["HK", 123])
    path = _save(workbook, tmp_path / "f1.xlsx")

    result = await excel.parse_excel(str(path))

    assert result.success is True and result.error is None
    assert result.json == {
        "sheets": 3,
        "sheet_names": ["Tasks", "Empty", "Totals"],
        "sheets_not_read": [],
        "sheet_errors": {},
    }
    tables = [block for block in result.blocks if block.type == "table"]
    assert [block.sheet for block in tables] == ["Tasks", "Totals"]
    # The caption names the sheet; a line break inside a cell stays on its table row and a pipe is escaped.
    assert tables[0].content == (
        'Sheet "Tasks": rows 1-4.\n\n'
        "| Task | Owner | Note |\n"
        "| --- | --- | --- |\n"
        "| Build | Ann | line one line two \\| done |\n"
        "| Ship | Bo | 3.5 |"
    )
    assert tables[0].row_range == "1-4"
    assert tables[0].table_json[1] == ["Build", "Ann", "line one\nline two | done"]
    # The blank row is dropped, and the row paragraphs keep the sheet's row numbers.
    paragraphs = [(block.row_range, block.content) for block in result.blocks if block.type == "paragraph" and block.sheet == "Tasks"]
    assert paragraphs[-1] == ("4-4", "Ship | Bo | 3.5")
    assert 'Sheet "Totals": rows 1-2.' in result.markdown

    # The workbook is closed: the file can go (a held handle blocks this on Windows).
    path.unlink()


@pytest.mark.asyncio
async def test_parse_excel_cuts_a_sheet_at_the_row_limit_and_says_so(tmp_path):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    for index in range(1, 21):
        sheet.append([f"row {index}", index])
    path = _save(workbook, tmp_path / "f2.xlsx")

    result = await excel.parse_excel(str(path), {"max_rows": 5})

    table = result.blocks[0]
    assert table.content.startswith('Sheet "Data": rows 1-5 of 20 only, the sheet has more rows.')
    assert "row 5" in table.content and "row 6" not in table.content
    assert table.row_range == "1-5"
    # A cut sheet is not also repeated row by row.
    assert [block.type for block in result.blocks] == ["table"]


@pytest.mark.asyncio
async def test_parse_excel_stops_at_the_workbook_budget_and_names_the_sheets_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(excel, "MAX_WORKBOOK_CHARS", 2000)
    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "Big"
    for index in range(1, 501):
        first.append([f"task number {index}", "a fairly long piece of text for this row", index])
    later = workbook.create_sheet("Later")
    later.append(["never", "read"])
    path = _save(workbook, tmp_path / "f3.xlsx")

    result = await excel.parse_excel(str(path))

    assert result.success is True
    table = result.blocks[0]
    assert "only, the sheet has more rows." in table.content.splitlines()[0]
    # The budget bounds the text, give or take the row that crossed it.
    assert 2000 <= len(table.content) < 2400
    assert result.json["sheets_not_read"] == ["Later"]
    assert result.blocks[-1].type == "paragraph"
    assert '"Later"' in result.blocks[-1].content and "not in this excerpt" in result.blocks[-1].content
    assert "never" not in result.markdown


@pytest.mark.asyncio
async def test_parse_excel_does_not_trust_the_dimension_record(tmp_path):
    """A streamed export can leave ``A1`` (or the whole grid) as the sheet's dimension."""
    for name, declared in (("a1.xlsx", b"A1"), ("grid.xlsx", b"A1:XFD1048576")):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Export"
        for index in range(1, 31):
            sheet.append([f"row {index}", index, "x"])
        path = _save(workbook, tmp_path / name)

        def _declare(data: bytes, declared: bytes = declared) -> bytes:
            assert b'<dimension ref="A1:C30"' in data
            return data.replace(b'<dimension ref="A1:C30"', b'<dimension ref="' + declared + b'"', 1)

        _with_sheet_xml(path, _declare)
        result = await excel.parse_excel(str(path))

        assert result.success is True, result.error
        table = result.blocks[0]
        assert table.content.startswith('Sheet "Export": rows 1-30.'), name
        assert len(table.table_json) == 30 and table.table_json[29] == ["row 30", "30", "x"], name


@pytest.mark.asyncio
async def test_parse_excel_keeps_the_leading_columns_of_a_very_wide_sheet(tmp_path):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Wide"
    sheet.append([f"c{index}" for index in range(1, excel.MAX_SHEET_COLUMNS + 6)])
    path = _save(workbook, tmp_path / "f5.xlsx")

    result = await excel.parse_excel(str(path))

    table = result.blocks[0]
    assert table.content.startswith(
        f'Sheet "Wide": rows 1-1; columns after the first {excel.MAX_SHEET_COLUMNS} are left out.'
    )
    assert len(table.table_json[0]) == excel.MAX_SHEET_COLUMNS
    assert table.table_json[0][-1] == f"c{excel.MAX_SHEET_COLUMNS}"


@pytest.mark.asyncio
async def test_parse_excel_reads_the_workbook_off_the_event_loop(tmp_path, monkeypatch):
    workbook = openpyxl.Workbook()
    workbook.active.append(["a", "b"])
    path = _save(workbook, tmp_path / "f6.xlsx")
    seen = {}
    real = excel._parse_excel_sync

    def _recording(file_path, options):
        seen["thread"] = threading.current_thread()
        return real(file_path, options)

    monkeypatch.setattr(excel, "_parse_excel_sync", _recording)
    result = await excel.parse_excel(str(path))

    assert result.success is True
    assert seen["thread"] is not threading.current_thread()


@pytest.mark.asyncio
async def test_parse_excel_reports_a_file_that_is_not_a_workbook_and_releases_it(tmp_path):
    path = tmp_path / "f7.xlsx"
    path.write_bytes(b"PK\x03\x04 not a workbook")

    result = await excel.parse_excel(str(path))

    assert result.success is False
    assert result.error
    assert result.content_type == excel.XLSX_CONTENT_TYPE
    path.unlink()


def test_table_markdown_is_not_padded_to_the_widest_cell():
    rows = [["name", "note"], ["a", "x" * 500], ["b", "short"]]
    markdown = excel._table_to_markdown(rows)
    assert markdown.splitlines()[1] == "| --- | --- |"
    assert markdown.splitlines()[3] == "| b | short |"
    # A ragged row is filled out to the table's width.
    assert excel._table_to_markdown([["a", "b", "c"], ["1"]]).splitlines()[2] == "| 1 |  |  |"


@pytest.mark.asyncio
async def test_a_very_long_cell_is_cut(tmp_path):
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.active.append(["note", "x" * 30000])
    path = tmp_path / "f9.xlsx"
    workbook.save(path)

    result = await excel.parse_excel(str(path))

    assert result.success is True
    assert result.blocks[0].table_json[0] == ["note", "x" * excel.MAX_CELL_CHARS + "..."]
