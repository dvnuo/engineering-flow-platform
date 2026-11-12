"""The pdf attachment parser.

The runtime image never shipped a PDF library: the parser imported PyMuPDF and
pdfplumber, neither is in requirements.txt, and every PDF a member attached
answered "No content extracted" with a 400.

- requirements.txt ships pypdf (BSD) for the text layer. PyMuPDF is AGPL and
  stays out; the parser uses it only where it is installed.
- The file is read on a worker thread and its handle is released.
- What a PDF contributes is bounded in pages, characters and time; the text
  says where it stops and which pages have none.
- A PDF without a text layer, a password-protected one and a damaged one fail
  with a reason.

The fixtures are assembled here, object by object: no binary file is committed
and the text they hold does not depend on the library under test.
"""
import io
import sys
import threading
import types
from pathlib import Path

import pytest

pypdf = pytest.importorskip("pypdf")

from src.utils.file_parser import pdf  # noqa: E402
from tests import test_runtime_api_chat_attachments as chat_attachment_api  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

# The upload/parse round trip runs on the storage isolation the chat attachment
# API tests already have.
isolated_attachment_storage = chat_attachment_api.isolated_attachment_storage


def _assemble(objects) -> bytes:
    """A PDF file from its object bodies; object 1 is the catalog."""
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def _stream(data: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"


def _document(contents, font: bytes, extra_objects=()) -> bytes:
    """One page per content stream, all using font object 3 as /F1; extra objects start at 4."""
    objects = [b"", b"", font, *extra_objects]
    kids = []
    for content in contents:
        objects.append(_stream(content))
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % len(objects)
        )
        kids.append(len(objects))
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % kid for kid in kids), len(kids))
    return _assemble(objects)


def _text_pdf(pages) -> bytes:
    """A PDF whose pages hold the given lines (plain ASCII, no parentheses); an empty page has no text."""
    contents = []
    for lines in pages:
        shown = b"".join(b"(%s) Tj T*\n" % line.encode("ascii") for line in lines)
        contents.append(b"BT /F1 12 Tf 72 720 Td 14 TL\n" + shown + b"ET" if lines else b"")
    return _document(contents, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")


def _unicode_pdf(lines) -> bytes:
    """A one-page PDF in a two-byte font whose ToUnicode map carries the text, as Chinese PDFs do."""
    chars = sorted({char for line in lines for char in line})
    code = {char: index for index, char in enumerate(chars, 1)}
    mappings = b"\n".join(b"<%04X> <%s>" % (code[char], char.encode("utf-16-be").hex().encode()) for char in chars)
    to_unicode = (
        b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        b"/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        b"%d beginbfchar\n%s\nendbfchar\n"
        b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend" % (len(chars), mappings)
    )
    shown = b"".join(b"<%s> Tj T*\n" % b"".join(b"%04X" % code[char] for char in line) for line in lines)
    return _document(
        [b"BT /F1 12 Tf 72 720 Td 16 TL\n" + shown + b"ET"],
        b"<< /Type /Font /Subtype /Type0 /BaseFont /Fixture /Encoding /Identity-H "
        b"/DescendantFonts [4 0 R] /ToUnicode 5 0 R >>",
        extra_objects=[
            b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Fixture "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000 >>",
            _stream(to_unicode),
        ],
    )


def _encrypted(data: bytes, user_password: str) -> bytes:
    writer = pypdf.PdfWriter(clone_from=io.BytesIO(data))
    writer.encrypt(user_password=user_password, owner_password="owner", algorithm="AES-256")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _page_blocks(result):
    return [(block.page, block.content) for block in result.blocks if block.page]


def test_requirements_ship_pypdf_and_no_agpl_pdf_library():
    """The image installs requirements.txt only: a parser whose library is not listed cannot run there."""
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    packages = {
        line.split("#")[0].split(">")[0].split("=")[0].split("<")[0].split("[")[0].strip()
        for line in requirements.splitlines()
    }
    assert "pypdf" in packages
    # PyMuPDF is AGPL-3.0. Listing it is a licensing decision for the platform, not a dependency bump.
    assert not packages & {"pymupdf", "fitz", "pymupdf4llm"}


@pytest.mark.asyncio
async def test_parse_pdf_returns_the_text_of_each_page(tmp_path):
    path = _write(tmp_path, "f1.pdf", _text_pdf([
        ["Quarterly report", "Revenue grew 12% in Q3.", "Churn fell to 3%."],
        ["Open tasks: 7"],
    ]))

    result = await pdf.parse_pdf(str(path))

    assert result.success is True and result.error is None
    assert result.content_type == "application/pdf"
    assert _page_blocks(result) == [
        (1, "Quarterly report\nRevenue grew 12% in Q3.\nChurn fell to 3%."),
        (2, "Open tasks: 7"),
    ]
    assert [(block.type, block.method, block.chunk_id) for block in result.blocks] == [
        ("paragraph", "pypdf", "f1_pdf_1_1"),
        ("paragraph", "pypdf", "f1_pdf_2_1"),
    ]
    assert result.json == {
        "pdf_type": "text",
        "pages": 2,
        "pages_read": 2,
        "pages_with_text": 2,
        "truncated": False,
        "tables": 0,
    }
    assert "--- Page 1 ---" in result.markdown and "--- Page 2 ---" in result.markdown
    assert "Revenue grew 12% in Q3." in result.markdown
    # A complete document carries no note about what is missing.
    assert "Note on this" not in result.markdown

    # The file is closed: it can go (a held handle blocks this on Windows).
    path.unlink()


@pytest.mark.asyncio
async def test_parse_pdf_reads_chinese_text(tmp_path):
    lines = ["季度報告", "收入增長 12%", "Mixed 中文 text"]
    path = _write(tmp_path, "f2.pdf", _unicode_pdf(lines))

    result = await pdf.parse_pdf(str(path))

    assert result.success is True, result.error
    assert _page_blocks(result) == [(1, "\n".join(lines))]


@pytest.mark.asyncio
async def test_parse_pdf_stops_at_the_page_limit_and_says_so(tmp_path):
    path = _write(tmp_path, "f3.pdf", _text_pdf([[f"text of page {number}"] for number in range(1, 6)]))

    result = await pdf.parse_pdf(str(path), {"max_pages": 2})

    assert result.success is True
    # The note leads, so it survives a prompt that keeps only the head of the text.
    assert result.blocks[0].page is None
    assert result.blocks[0].content == (
        "Note on this 5-page PDF: The text stops at page 2 of 5: the document is longer than the excerpt, "
        "and the rest of it is not here."
    )
    assert _page_blocks(result) == [(1, "text of page 1"), (2, "text of page 2")]
    assert result.json["pages"] == 5 and result.json["pages_read"] == 2 and result.json["truncated"] is True
    assert "text of page 3" not in result.markdown


@pytest.mark.asyncio
async def test_parse_pdf_stops_at_the_character_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "MAX_PDF_CHARS", 500)
    page = [f"line {number:02d} of a fairly long page" for number in range(1, 13)]
    path = _write(tmp_path, "f4.pdf", _text_pdf([page, page, ["never read"], ["never read"]]))

    result = await pdf.parse_pdf(str(path))

    assert result.success is True
    pages = _page_blocks(result)
    assert [number for number, _text in pages] == [1, 2]
    assert pages[0][1] == "\n".join(page)
    # The second page is cut where the budget ends.
    assert sum(len(text) for _number, text in pages) <= 500
    assert pages[1][1] and len(pages[1][1]) < len(pages[0][1])
    assert "stops at page 2 of 4, partway through the page" in result.blocks[0].content
    assert result.json["pages_read"] == 2 and result.json["truncated"] is True
    assert "never read" not in result.markdown


@pytest.mark.asyncio
async def test_parse_pdf_stops_at_the_time_budget_after_the_first_page(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "MAX_PARSE_SECONDS", -1)
    path = _write(tmp_path, "f5.pdf", _text_pdf([["first"], ["second"], ["third"]]))

    result = await pdf.parse_pdf(str(path))

    assert result.success is True
    assert _page_blocks(result) == [(1, "first")]
    assert "The text stops at page 1 of 3:" in result.blocks[0].content


@pytest.mark.asyncio
async def test_parse_pdf_names_the_pages_that_have_no_text(tmp_path):
    path = _write(tmp_path, "f6.pdf", _text_pdf([["cover"], [], [], ["appendix"], []]))

    result = await pdf.parse_pdf(str(path))

    assert result.success is True
    assert result.blocks[0].content == (
        "Note on this 5-page PDF: Pages with no text (scanned images are not read): 2-3, 5."
    )
    assert _page_blocks(result) == [(1, "cover"), (4, "appendix")]
    assert result.json["pages_with_text"] == 2 and result.json["truncated"] is False


@pytest.mark.asyncio
async def test_parse_pdf_without_a_text_layer_fails_with_the_reason(tmp_path):
    path = _write(tmp_path, "f7.pdf", _text_pdf([[], []]))

    result = await pdf.parse_pdf(str(path))

    assert result.success is False
    assert result.blocks == []
    assert result.error == (
        "No text could be extracted from this PDF: its pages have no text layer (a scanned document?)"
    )
    path.unlink()


@pytest.mark.asyncio
async def test_parse_pdf_reads_a_restricted_file_and_reports_a_password_protected_one(tmp_path):
    data = _text_pdf([["confidential terms"]])
    # No password to open, only an owner password that restricts printing and copying.
    restricted = _write(tmp_path, "f8.pdf", _encrypted(data, ""))
    locked = _write(tmp_path, "f9.pdf", _encrypted(data, "secret"))

    result = await pdf.parse_pdf(str(restricted))
    assert result.success is True, result.error
    assert _page_blocks(result) == [(1, "confidential terms")]

    result = await pdf.parse_pdf(str(locked))
    assert result.success is False
    assert result.error == "The PDF is password-protected and cannot be read without its password"
    locked.unlink()


@pytest.mark.asyncio
async def test_parse_pdf_reports_a_file_that_is_not_a_pdf_and_releases_it(tmp_path):
    path = _write(tmp_path, "f10.pdf", b"%PDF-1.4\nnot a pdf at all")

    result = await pdf.parse_pdf(str(path))

    assert result.success is False
    assert result.error
    assert result.content_type == "application/pdf"
    path.unlink()


@pytest.mark.asyncio
async def test_parse_pdf_reads_the_file_off_the_event_loop(tmp_path, monkeypatch):
    path = _write(tmp_path, "f11.pdf", _text_pdf([["a"]]))
    seen = {}
    real = pdf._parse_pdf_sync

    def _recording(file_path, options):
        seen["thread"] = threading.current_thread()
        return real(file_path, options)

    monkeypatch.setattr(pdf, "_parse_pdf_sync", _recording)
    result = await pdf.parse_pdf(str(path))

    assert result.success is True
    assert seen["thread"] is not threading.current_thread()


def test_a_page_that_cannot_be_read_does_not_cost_the_other_pages():
    def _page_text(index):
        if index == 1:
            raise ValueError("broken content stream")
        return f"page {index + 1}"

    read = pdf._read_pages(3, _page_text, max_pages=10, deadline=float("inf"), method="pypdf")

    assert read["pages"] == [(1, "page 1"), (3, "page 3")]
    assert read["page_errors"] == {2: "broken content stream"}
    assert read["pages_read"] == 3 and read["truncated"] is False
    assert pdf._excerpt_note(read) == "Note on this 3-page PDF: Pages that could not be read: 2."


def test_page_text_is_stored_without_nuls_lone_surrogates_or_runs_of_blank_lines():
    cleaned = pdf._clean_page_text("total\x00 due  \n\n\n\n\nHK$ 1,200 \ud83d\n")

    assert cleaned == "total due\n\nHK$ 1,200 �"
    cleaned.encode("utf-8")
    # A surrogate pair is the character it spells.
    assert pdf._clean_page_text("ok " + chr(0xD83D) + chr(0xDE00)) == "ok " + chr(0x1F600)
    assert pdf._clean_page_text(None) == ""


@pytest.mark.asyncio
async def test_parse_pdf_reads_a_radical_as_the_ideograph_it_stands_for(tmp_path):
    """A browser-printed PDF names 長, 戶, 至, 行, 二 and 頁 by radical code points that look the same."""
    printed = "行政⻑官：客⼾增⻑⾄第⼆⾴，⾏政區"
    path = _write(tmp_path, "f16.pdf", _unicode_pdf([printed]))

    result = await pdf.parse_pdf(str(path))

    assert _page_blocks(result) == [(1, "行政長官：客戶增長至第二頁，行政區")]


def test_every_radical_maps_to_a_unified_ideograph():
    import unicodedata

    table = pdf._RADICAL_TO_IDEOGRAPH
    # All 214 Kangxi radicals and the CJK Radicals Supplement but its repeat mark (U+2E80).
    assert len(table) == 214 + 114
    # By block rather than by unicodedata.name: the interpreter's Unicode data can be
    # older than a target (U+2B739 is Unicode 15.0, and Python 3.11 carries 14.0).
    unified_ideographs = (
        (0x3400, 0x4DBF),  # Extension A
        (0x4E00, 0x9FFF),  # CJK Unified Ideographs
        (0x20000, 0x2A6DF),  # Extension B
        (0x2A700, 0x2B739),  # Extension C
    )
    assert all(any(first <= target <= last for first, last in unified_ideographs) for target in table.values())
    assert chr(table[0x2ED1]) == "長" and chr(table[0x2FA7]) == "長" and chr(table[0x2F3E]) == "戶"
    # The two supplement radicals Unicode also gives a compatibility decomposition agree with it.
    for radical in (0x2E9F, 0x2EF3):
        assert chr(table[radical]) == unicodedata.normalize("NFKC", chr(radical))


@pytest.mark.asyncio
async def test_parse_pdf_without_any_pdf_library_says_which_one_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "_get_pypdf", lambda: None)
    monkeypatch.setattr(pdf, "_get_fitz", lambda: None)
    path = _write(tmp_path, "f12.pdf", _text_pdf([["a"]]))

    result = await pdf.parse_pdf(str(path))

    assert result.success is False
    assert result.error == "pypdf not installed"


class _FakeFitzPage:
    def __init__(self, text):
        self._text = text

    def get_text(self, _kind):
        return self._text

    def get_pixmap(self, matrix=None):
        return types.SimpleNamespace(tobytes=lambda _format: b"rendered page")


class _FakeFitzDocument:
    def __init__(self, texts):
        self._pages = [_FakeFitzPage(text) for text in texts]
        self.page_count = len(texts)
        self.closed = 0

    def __getitem__(self, index):
        return self._pages[index]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed += 1


def _fake_fitz(texts):
    document = _FakeFitzDocument(texts)
    return types.SimpleNamespace(open=lambda _path: document, Matrix=lambda x, y: (x, y), document=document)


@pytest.mark.asyncio
async def test_pymupdf_is_a_second_text_extractor_where_it_is_installed(tmp_path, monkeypatch):
    fake = _fake_fitz(["text PyMuPDF found", ""])
    monkeypatch.setattr(pdf, "_get_pypdf", lambda: None)
    monkeypatch.setattr(pdf, "_get_fitz", lambda: fake)
    path = _write(tmp_path, "f13.pdf", _text_pdf([["a"], []]))

    result = await pdf.parse_pdf(str(path))

    assert result.success is True
    assert [(block.page, block.content, block.method) for block in result.blocks if block.page] == [
        (1, "text PyMuPDF found", "pymupdf")
    ]
    assert fake.document.closed == 1


@pytest.mark.asyncio
async def test_a_scanned_pdf_is_ocred_only_where_pymupdf_can_render_it(tmp_path, monkeypatch):
    fake = _fake_fitz(["", ""])
    rendered = []

    def _ocr(png):
        rendered.append(png)
        return "words on the scan"

    monkeypatch.setattr(pdf, "_get_fitz", lambda: fake)
    monkeypatch.setattr(pdf, "_ocr_page_image", _ocr)
    path = _write(tmp_path, "f14.pdf", _text_pdf([[], []]))

    result = await pdf.parse_pdf(str(path))

    # pypdf and PyMuPDF found no text layer, so the pages were rendered and read.
    assert result.success is True
    assert rendered == [b"rendered page", b"rendered page"]
    assert [(block.page, block.content, block.method, block.confidence) for block in result.blocks] == [
        (1, "words on the scan", "ocr", 0.8),
        (2, "words on the scan", "ocr", 0.8),
    ]
    assert result.json["pdf_type"] == "scanned"


@pytest.mark.asyncio
async def test_why_a_file_could_not_be_opened_outlives_a_reader_that_finds_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "_get_fitz", lambda: _fake_fitz([""]))
    monkeypatch.setattr(pdf, "_ocr_page_image", lambda _png: "")
    locked = _write(tmp_path, "f17.pdf", _encrypted(_text_pdf([["confidential terms"]]), "secret"))

    result = await pdf.parse_pdf(str(locked))

    assert result.success is False
    assert result.error == "The PDF is password-protected and cannot be read without its password"


@pytest.mark.asyncio
async def test_pdfplumber_adds_the_tables_of_the_pages_read_where_it_is_installed(tmp_path, monkeypatch):
    table = [["Region", "Total"], ["HK", "123"]]

    class _Plumbed:
        pages = [
            types.SimpleNamespace(extract_tables=lambda: [table, []]),
            types.SimpleNamespace(extract_tables=lambda: []),
            types.SimpleNamespace(extract_tables=lambda: [[["beyond", "the page limit"]]]),
        ]

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

    monkeypatch.setitem(sys.modules, "pdfplumber", types.SimpleNamespace(open=lambda _path: _Plumbed()))
    path = _write(tmp_path, "f15.pdf", _text_pdf([["Totals by region"], ["Notes"], ["Appendix"]]))

    result = await pdf.parse_pdf(str(path), {"max_pages": 2})

    assert [(block.page, block.type, block.method) for block in result.blocks if block.page] == [
        (1, "paragraph", "pypdf"),
        (1, "table", "pdfplumber"),
        (2, "paragraph", "pypdf"),
    ]
    table_block = result.blocks[2]
    assert table_block.table_json == table
    assert "Region" in table_block.markdown and "HK" in table_block.markdown
    assert result.json["tables"] == 1
    assert "beyond" not in result.markdown


@pytest.mark.asyncio
async def test_pdf_upload_is_parsed_into_the_chat_context(isolated_attachment_storage):
    """The path a member's attachment takes: upload, parse, and the text is there for the prompt."""
    from src.gateway import runtime_api

    data = _text_pdf([["Supplier agreement", "Payment is due within 30 days."], ["Signed in Hong Kong"]])
    client = await chat_attachment_api._client()
    try:
        upload = await client.post(
            "/api/files/upload",
            params={"session_id": "s1"},
            data=chat_attachment_api._file_form("Supplier agreement (final).pdf", data, "application/octet-stream"),
        )
        assert upload.status == 201, await upload.text()
        uploaded = await upload.json()
        assert uploaded["content_type"] == "application/pdf"
        file_id = uploaded["file_id"]

        parsed = await client.post("/api/files/parse", params={"session_id": "s1"}, json={"file_id": file_id})
        assert parsed.status == 200, await parsed.text()
        body = await parsed.json()
        assert body["success"] is True
        assert body["filename"] == "Supplier agreement (final).pdf"
        assert "Payment is due within 30 days." in body["markdown"]
        assert [block["page"] for block in body["blocks"]] == [1, 2]
        assert body["json"]["pages"] == 2
        assert body["saved_chunks"] == 2
    finally:
        await client.close()

    context = await runtime_api._ensure_chat_attachment_context(session_id="s1", attachment_ids=[file_id])
    assert context["context_file_ids"] == [file_id]
    assert context["failures"] == []
    prompt = runtime_api._build_direct_attachment_context_prompt(
        session_id="s1", file_ids=[file_id], user_question="When is payment due?"
    )
    assert "Payment is due within 30 days." in prompt and "Signed in Hong Kong" in prompt
