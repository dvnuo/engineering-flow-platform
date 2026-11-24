"""Every attachment is handed to the agent with its workspace path.

- The block lists each attached file with its path, type, size, the member's
  file name and what of it reached the model: a parsed document travels as an
  excerpt, which is a preview; processing the file takes the path.
- A parsed file that the top-k retrieval or the fallback budget left out of
  the prompt is marked so instead of being called excerpted.
- Images reach the model as image parts only when the deployment switch is
  on (EFP_CHAT_VISION, or its older name EFP_COPILOT_VISION_VIA_CHAT),
  whatever the chat provider; otherwise they are marked as not readable.
- The office formats the runtime does not parse (doc, dot, xls) are always
  marked as not readable and never parsed.
- For the files marked as not readable the block says how this chat analyzes
  them: an image with the inspect-image CLI when the Image analysis connector
  is configured; everything else, and an image inspect-image could not read,
  with the attached-files analysis skill when the Local bridge connector is
  on; otherwise the agent must tell the member it cannot.
"""
import asyncio

import pytest

from src.efp_runtime.llm.vision import CHAT_VISION_ENV, COPILOT_VISION_VIA_CHAT_ENV
from src.gateway.attachment_handoff import (
    ATTACHED_FILES_HEADER,
    ATTACHED_FILES_RULE,
    HANDOFF_HEADER,
    HANDOFF_IMAGE_ANALYSIS_HINT,
    HANDOFF_IMAGES_FALLBACK_WITH_BRIDGE,
    HANDOFF_IMAGES_FALLBACK_WITHOUT_BRIDGE,
    HANDOFF_IMAGES_INSPECT,
    HANDOFF_OFFICE_WITH_BRIDGE,
    HANDOFF_OFFICE_WITHOUT_BRIDGE,
    HANDOFF_WITH_BRIDGE,
    HANDOFF_WITHOUT_BRIDGE,
    build_attachment_handoff,
)
from src.utils.file_parser.validators import (
    DEFAULT_UPLOAD_EXTENSIONS,
    FORWARDED_MIME_TYPES,
    IMAGE_EXTENSIONS,
    is_forwarded_upload_mime,
    is_supported_upload_mime,
)


# --- the handoff block ----------------------------------------------------------


def test_build_attachment_handoff():
    files = [
        {"path": "/workspace/uploads/abc.png", "name": "shot.png", "content_type": "image/png", "size_bytes": 1234, "status": "unreadable"},
        {"path": "/workspace/uploads/def.xls", "name": "def.xls", "content_type": "application/vnd.ms-excel", "size_bytes": 99, "status": "unreadable"},
        {
            "path": "/workspace/uploads/ghi.xlsx",
            "name": "sales.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "size_bytes": 2048,
            "status": "excerpt",
        },
    ]
    # Without the Image analysis connector the bridge is the one route there is.
    text = build_attachment_handoff(files, bridge_available=True, image_analysis_configured=False)
    assert text.startswith(ATTACHED_FILES_HEADER)
    assert '- /workspace/uploads/abc.png (image/png, 1234 bytes, attached as "shot.png"; not readable by the chat model)' in text
    assert "- /workspace/uploads/def.xls (application/vnd.ms-excel, 99 bytes; not readable by the chat model)" in text
    assert (
        '- /workspace/uploads/ghi.xlsx (application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, 2048 bytes, '
        'attached as "sales.xlsx"; an excerpt of it is in this prompt)'
    ) in text
    # The rule comes after the list, the handoff wording after the rule.
    assert text.index(ATTACHED_FILES_RULE) > text.index("ghi.xlsx")
    assert text.index(HANDOFF_HEADER) > text.index(ATTACHED_FILES_RULE)
    assert HANDOFF_WITH_BRIDGE in text and HANDOFF_WITHOUT_BRIDGE not in text
    assert "Productivity Suite" in text and "inspect-image" not in text

    offline = build_attachment_handoff(files, bridge_available=False, image_analysis_configured=False)
    assert HANDOFF_WITHOUT_BRIDGE in offline and HANDOFF_WITH_BRIDGE not in offline
    # No bridge and an image: the block names the connector that reads images without one.
    assert offline.endswith(HANDOFF_IMAGE_ANALYSIS_HINT) and "inspect-image" not in offline

    # With the connector the image goes to inspect-image first; the spreadsheet stays with the bridge.
    configured = build_attachment_handoff(files, bridge_available=True, image_analysis_configured=True)
    assert configured.startswith(ATTACHED_FILES_HEADER)
    assert '- /workspace/uploads/abc.png (image/png, 1234 bytes, attached as "shot.png"; not readable by the chat model)' in configured
    assert "- /workspace/uploads/def.xls (application/vnd.ms-excel, 99 bytes; not readable by the chat model)" in configured
    assert configured.index(HANDOFF_HEADER) > configured.index(ATTACHED_FILES_RULE)
    assert configured.endswith(
        f"{HANDOFF_HEADER} {HANDOFF_IMAGES_INSPECT} {HANDOFF_IMAGES_FALLBACK_WITH_BRIDGE} {HANDOFF_OFFICE_WITH_BRIDGE}"
    )
    assert 'inspect-image inspect --image <path> --prompt "<what to read or explain>" --json' in configured
    assert "Productivity Suite" in configured and HANDOFF_WITH_BRIDGE not in configured
    assert build_attachment_handoff([], bridge_available=True, image_analysis_configured=True) == ""
    assert build_attachment_handoff([], bridge_available=True, image_analysis_configured=False) == ""


_UNREADABLE_IMAGE = {"path": "/workspace/uploads/abc.png", "name": "abc.png", "content_type": "image/png", "size_bytes": 1234, "status": "unreadable"}
_UNREADABLE_PHOTO = {"path": "/workspace/uploads/pqr.jpg", "name": "pqr.jpg", "content_type": "Image/JPEG", "size_bytes": 77, "status": "unreadable"}
_UNREADABLE_SHEET = {"path": "/workspace/uploads/def.xls", "name": "def.xls", "content_type": "application/vnd.ms-excel", "size_bytes": 99, "status": "unreadable"}
_UNREADABLE_LETTER = {"path": "/workspace/uploads/stu.doc", "name": "stu.doc", "content_type": "application/msword", "size_bytes": 55, "status": "unreadable"}
_PARSED_SHEET = {"path": "/workspace/uploads/ghi.xlsx", "name": "ghi.xlsx", "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "size_bytes": 2048, "status": "excerpt"}
_SENT_IMAGE = {"path": "/workspace/uploads/mno.png", "name": "mno.png", "content_type": "image/png", "size_bytes": 5, "status": "image_sent"}

_IMAGES = [_UNREADABLE_IMAGE, _PARSED_SHEET, _UNREADABLE_PHOTO]
_IMAGES_AND_OFFICE = [_UNREADABLE_IMAGE, _UNREADABLE_SHEET, _PARSED_SHEET]
# An image that was sent to the model is not an unreadable image.
_OFFICE = [_UNREADABLE_SHEET, _SENT_IMAGE, _UNREADABLE_LETTER]


@pytest.mark.parametrize(
    "configured, files, bridge, expected",
    [
        # Image analysis configured, unreadable images only.
        (True, _IMAGES, True, [HANDOFF_IMAGES_INSPECT, HANDOFF_IMAGES_FALLBACK_WITH_BRIDGE]),
        (True, _IMAGES, False, [HANDOFF_IMAGES_INSPECT, HANDOFF_IMAGES_FALLBACK_WITHOUT_BRIDGE]),
        # Configured, unreadable images and office files: the same, then the office text.
        (True, _IMAGES_AND_OFFICE, True, [HANDOFF_IMAGES_INSPECT, HANDOFF_IMAGES_FALLBACK_WITH_BRIDGE, HANDOFF_OFFICE_WITH_BRIDGE]),
        (True, _IMAGES_AND_OFFICE, False, [HANDOFF_IMAGES_INSPECT, HANDOFF_IMAGES_FALLBACK_WITHOUT_BRIDGE, HANDOFF_OFFICE_WITHOUT_BRIDGE]),
        # Configured, unreadable office files only: inspect-image has nothing to read.
        (True, _OFFICE, True, [HANDOFF_WITH_BRIDGE]),
        (True, _OFFICE, False, [HANDOFF_WITHOUT_BRIDGE]),
        # Not configured: the bridge wording, and the hint when images have no bridge.
        (False, _IMAGES, True, [HANDOFF_WITH_BRIDGE]),
        (False, _IMAGES, False, [HANDOFF_WITHOUT_BRIDGE, HANDOFF_IMAGE_ANALYSIS_HINT]),
        (False, _IMAGES_AND_OFFICE, True, [HANDOFF_WITH_BRIDGE]),
        (False, _IMAGES_AND_OFFICE, False, [HANDOFF_WITHOUT_BRIDGE, HANDOFF_IMAGE_ANALYSIS_HINT]),
        (False, _OFFICE, True, [HANDOFF_WITH_BRIDGE]),
        (False, _OFFICE, False, [HANDOFF_WITHOUT_BRIDGE]),
    ],
)
def test_what_follows_the_handoff_header(configured, files, bridge, expected):
    text = build_attachment_handoff(files, bridge_available=bridge, image_analysis_configured=configured)
    # The header, the file lines and the rule are what they always were...
    lines = text.split("\n")
    assert lines[0] == ATTACHED_FILES_HEADER
    assert lines[1 : 1 + len(files)] == [
        f"- {item['path']} ({item['content_type']}, {item['size_bytes']} bytes; "
        + {"unreadable": "not readable by the chat model", "excerpt": "an excerpt of it is in this prompt", "image_sent": "sent to the model as an image"}[item["status"]]
        + ")"
        for item in files
    ]
    assert lines[1 + len(files)] == ATTACHED_FILES_RULE
    # ...and one line follows: the handoff header and exactly the texts of this case, in order.
    assert lines[2 + len(files) :] == [" ".join([HANDOFF_HEADER, *expected])]
    every_text = [
        HANDOFF_WITH_BRIDGE,
        HANDOFF_WITHOUT_BRIDGE,
        HANDOFF_IMAGES_INSPECT,
        HANDOFF_IMAGES_FALLBACK_WITH_BRIDGE,
        HANDOFF_IMAGES_FALLBACK_WITHOUT_BRIDGE,
        HANDOFF_OFFICE_WITH_BRIDGE,
        HANDOFF_OFFICE_WITHOUT_BRIDGE,
        HANDOFF_IMAGE_ANALYSIS_HINT,
    ]
    for candidate in every_text:
        assert (candidate in text) is (candidate in expected), candidate


def test_the_handoff_texts_are_the_agreed_ones():
    """The agents' instructions and the skills name these sentences; a reworded one is a contract change."""
    assert HANDOFF_IMAGES_INSPECT == (
        "Image analysis is on for this assistant: read each image marked above as not readable with the inspect-image CLI "
        'before you answer. Run `inspect-image inspect --image <path> --prompt "<what to read or explain>" --json` from bash '
        "and answer from data.result.answer and data.result.visible_text; add --preset ocr, ui, diagram, chart, or error when "
        "it fits, and ask a follow-up question with another call. Never pass --model, never guess what an image shows, and "
        "never fall back to OCR or image-parsing scripts."
    )
    assert HANDOFF_IMAGES_FALLBACK_WITH_BRIDGE == (
        "If inspect-image answers auth_required or config_error, or still fails after one retry, analyze the images with the "
        "attached-files analysis skill named in your instructions instead (Productivity Suite through the user's own browser, "
        "the browser tool's workflow.run), asking the user before uploading."
    )
    assert HANDOFF_IMAGES_FALLBACK_WITHOUT_BRIDGE == (
        "If inspect-image answers auth_required or config_error, or still fails after one retry, tell the user which images "
        "you could not read and that the Image analysis connector under Portal > Connectors needs checking, and continue with "
        "what the message says in words."
    )
    assert HANDOFF_OFFICE_WITH_BRIDGE == (
        "inspect-image reads images only. For the office files marked above as not readable, the Local bridge is on for this "
        "chat: analyze them with the attached-files analysis skill named in your instructions (Productivity Suite through the "
        "user's own browser, the browser tool's workflow.run). Ask the user before uploading and report the result as that "
        "system's output."
    )
    assert HANDOFF_OFFICE_WITHOUT_BRIDGE == (
        "inspect-image reads images only. The Local bridge is not on for this chat, so the office files marked above as not "
        "readable cannot be analyzed here: tell the user which files you could not read and that an interactive chat with the "
        "Local bridge switched on can analyze them."
    )
    assert HANDOFF_IMAGE_ANALYSIS_HINT == (
        "Images can also be read without the bridge once the Image analysis connector is set up under Portal > Connectors."
    )


@pytest.mark.parametrize("configured", [True, False])
@pytest.mark.parametrize("bridge", [True, False])
def test_build_attachment_handoff_for_readable_files_only_skips_the_analysis_wording(configured, bridge):
    files = [
        {"path": "/workspace/uploads/ghi.xlsx", "name": "sales.xlsx", "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "size_bytes": 2048, "status": "excerpt"},
        {"path": "/workspace/uploads/jkl.csv", "name": "jkl.csv", "content_type": "text/csv", "size_bytes": 10, "status": "not_parsed"},
        {"path": "/workspace/uploads/mno.png", "name": "mno.png", "content_type": "image/png", "size_bytes": 5, "status": "image_sent"},
    ]
    text = build_attachment_handoff(files, bridge_available=bridge, image_analysis_configured=configured)
    assert text.startswith(ATTACHED_FILES_HEADER) and text.endswith(ATTACHED_FILES_RULE)
    assert "- /workspace/uploads/jkl.csv (text/csv, 10 bytes; it could not be parsed, read it from its path)" in text
    assert "- /workspace/uploads/mno.png (image/png, 5 bytes; sent to the model as an image)" in text
    assert HANDOFF_HEADER not in text and HANDOFF_WITHOUT_BRIDGE not in text and HANDOFF_WITH_BRIDGE not in text
    assert "inspect-image" not in text and HANDOFF_IMAGE_ANALYSIS_HINT not in text
    assert "never ask the user where the file is" in text


# --- validators --------------------------------------------------------------------


def test_forwarded_and_image_formats_are_accepted_but_not_parsed():
    for ext in ("doc", "dot", "xls", "png", "jpg", "jpeg", "jpe", "jfif", "tiff", "webp", "bmp"):
        assert ext in DEFAULT_UPLOAD_EXTENSIONS, ext
    assert "gif" not in DEFAULT_UPLOAD_EXTENSIONS
    assert {"jpe", "jfif", "tiff", "bmp"} <= IMAGE_EXTENSIONS
    assert FORWARDED_MIME_TYPES == {"application/msword", "application/vnd.ms-excel"}
    assert is_forwarded_upload_mime("application/msword")
    assert is_forwarded_upload_mime("Application/VND.MS-Excel; charset=binary")
    assert not is_forwarded_upload_mime("application/pdf") and not is_forwarded_upload_mime("")
    for mime in ("application/msword", "application/vnd.ms-excel", "image/tiff", "image/bmp"):
        assert is_supported_upload_mime(mime), mime


def test_content_signatures_decide_the_forwarded_types():
    from src.utils.file_parser import _detect_mime_type

    ole2 = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\x00" * 64
    assert _detect_mime_type(ole2, "book.xls") == "application/vnd.ms-excel"
    assert _detect_mime_type(ole2, "letter.doc") == "application/msword"
    assert _detect_mime_type(ole2, "template.dot") == "application/msword"
    assert _detect_mime_type(b"II*\x00" + b"\x00" * 64, "scan.tiff") == "image/tiff"
    assert _detect_mime_type(b"MM\x00*" + b"\x00" * 64, "scan.tiff") == "image/tiff"
    assert _detect_mime_type(b"BM" + b"\x00" * 64, "pic.bmp") == "image/bmp"
    assert _detect_mime_type(b"\xFF\xD8\xFF\xE0" + b"\x00" * 64, "photo.jfif") == "image/jpeg"
    # A renamed binary is still refused: the extension never decides.
    assert _detect_mime_type(b"MZ" + b"\x00" * 64, "tool.xls") == "application/octet-stream"


@pytest.mark.asyncio
async def test_parse_file_refuses_forwarded_formats_with_a_reason(monkeypatch, tmp_path):
    import src.utils.file_parser as file_parser

    stored = tmp_path / "book.xls"
    stored.write_bytes(b"\xD0\xCF\x11\xE0" + b"\x00" * 16)

    class _Meta:
        content_type = "application/vnd.ms-excel"
        original_filename = "book.xls"

    monkeypatch.setattr(file_parser, "get_file_path", lambda file_id: stored)
    monkeypatch.setattr(file_parser, "get_metadata", lambda file_id: _Meta())
    result = await file_parser.parse_file("f-xls")
    assert result.success is False
    assert "Productivity Suite" in str(result.error)
    assert result.filename == "book.xls" and result.file_id == "f-xls"


# --- the chat gateway ------------------------------------------------------------

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _handoff_chat_fixture(monkeypatch, tmp_path, *, provider, bridge: bool, image_analysis: bool = False):
    from src.gateway import runtime_api
    from src.utils.file_parser import storage

    # The switch has two names; a test that wants it on sets it after this.
    monkeypatch.delenv(CHAT_VISION_ENV, raising=False)
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    # What boot found of the Image analysis connector (GET /ready reports the same).
    image_analysis_state = (
        {"configured": True, "reason": None, "model": "gpt-5.4", "config_path": "/root/.efp/inspect-image.yaml"}
        if image_analysis
        else {"configured": False, "reason": "image analysis is off for this profile", "model": None, "config_path": None}
    )
    monkeypatch.setattr(runtime_api, "get_image_analysis_state", lambda: dict(image_analysis_state))

    captured = {}
    shot = tmp_path / "shot.png"
    book = tmp_path / "book.xls"
    sheet = tmp_path / "sheet.xlsx"
    notes = tmp_path / "notes.csv"
    shot.write_bytes(b"one")
    book.write_bytes(b"two")
    sheet.write_bytes(b"four")
    notes.write_bytes(b"a,b\n")

    class _Meta:
        def __init__(self, file_id: str, content_type: str, name: str):
            self.file_id = file_id
            self.session_id = "s1"
            self.content_type = content_type
            self.original_filename = name

    metadata_map = {
        "f1": _Meta("f1", "image/png", "f1-shot.png"),
        "f2": _Meta("f2", "application/vnd.ms-excel", "f2-book.xls"),
        "f3": _Meta("f3", XLSX_MIME, "f3-sales.xlsx"),
        "f4": _Meta("f4", "text/csv", "f4-notes.csv"),
    }
    file_map = {"f1": shot, "f2": book, "f3": sheet, "f4": notes}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

    trusted = {"connectors": {"local_bridge": {"enabled": True, "client_id": "tab-1"}}} if bridge else {}
    monkeypatch.setattr(runtime_api, "_extract_trusted_control_plane_metadata", lambda request, data: dict(trusted))
    monkeypatch.setattr(runtime_api, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(runtime_api, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(runtime_api.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5.6-terra", "provider": provider}}, raising=False)
    monkeypatch.setattr(runtime_api.session_manager, "_initialized", True)
    monkeypatch.setattr(runtime_api.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(runtime_api.runtime_session_artifacts, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(runtime_api, "get_metadata", lambda file_id: metadata_map[file_id])
    monkeypatch.setattr(storage, "get_file_path", lambda file_id: file_map[file_id])
    return runtime_api, captured, shot, book


class _Request:
    app = {}
    headers = {}

    def __init__(self, payload):
        self._payload = payload
        self._items = {}

    async def json(self):
        return self._payload

    # api_chat parks the attachment citations on the request (``request['file_citations']``).
    def __setitem__(self, key, value):
        self._items[key] = value

    def __getitem__(self, key):
        return self._items[key]

    def get(self, key, default=None):
        return self._items.get(key, default)


@pytest.mark.asyncio
async def test_api_chat_hands_images_and_forwarded_files_to_the_agent_with_the_bridge(monkeypatch, tmp_path):
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    runtime_api, captured, shot, book = _handoff_chat_fixture(monkeypatch, tmp_path, provider="github_copilot", bridge=True)

    response = await runtime_api.api_chat(_Request({"message": "what is in these?", "session_id": "s1", "attachments": ["f1", "f2"]}))
    assert response.status == 200
    # Nothing is inlined for a model that cannot see it, nothing is parsed...
    assert captured["attached_images"] is None
    assert captured["message"] == "what is in these?"
    # ...the agent is told where the files are and that the bridge can analyze them.
    handoff = captured["transient_model_message"]
    assert handoff.startswith(ATTACHED_FILES_HEADER)
    assert f"- {shot} (image/png, 3 bytes, attached as \"f1-shot.png\"; not readable by the chat model)" in handoff
    assert f"- {book} (application/vnd.ms-excel, 3 bytes, attached as \"f2-book.xls\"; not readable by the chat model)" in handoff
    assert ATTACHED_FILES_RULE in handoff
    # This profile has no Image analysis connector, so the bridge is the route for both files.
    assert HANDOFF_WITH_BRIDGE in handoff and HANDOFF_WITHOUT_BRIDGE not in handoff
    assert "inspect-image" not in handoff
    assert captured["attachments"] == ["f1", "f2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github_copilot", "ai_platform"])
async def test_api_chat_hands_images_to_inspect_image_and_forwarded_files_to_the_bridge(monkeypatch, tmp_path, provider):
    """The Image analysis connector is configured: inspect-image reads the image, whatever the chat provider."""
    runtime_api, captured, shot, book = _handoff_chat_fixture(
        monkeypatch, tmp_path, provider=provider, bridge=True, image_analysis=True
    )

    response = await runtime_api.api_chat(_Request({"message": "what is in these?", "session_id": "s1", "attachments": ["f1", "f2"]}))
    assert response.status == 200
    assert captured["attached_images"] is None
    handoff = captured["transient_model_message"]
    assert handoff.startswith(ATTACHED_FILES_HEADER)
    assert f"- {shot} (image/png, 3 bytes, attached as \"f1-shot.png\"; not readable by the chat model)" in handoff
    assert f"- {book} (application/vnd.ms-excel, 3 bytes, attached as \"f2-book.xls\"; not readable by the chat model)" in handoff
    # inspect-image first, Productivity Suite when it fails; the spreadsheet is the bridge's from the start.
    assert handoff.endswith(
        f"{HANDOFF_HEADER} {HANDOFF_IMAGES_INSPECT} {HANDOFF_IMAGES_FALLBACK_WITH_BRIDGE} {HANDOFF_OFFICE_WITH_BRIDGE}"
    )
    assert "inspect-image inspect --image <path>" in handoff
    assert HANDOFF_WITH_BRIDGE not in handoff and HANDOFF_WITHOUT_BRIDGE not in handoff

    # A background task or a chat with the browser toggle off still reads the image.
    captured.clear()
    runtime_api, captured, shot, book = _handoff_chat_fixture(
        monkeypatch, tmp_path, provider=provider, bridge=False, image_analysis=True
    )
    response = await runtime_api.api_chat(_Request({"message": "what is in these?", "session_id": "s1", "attachments": ["f1", "f2"]}))
    assert response.status == 200
    assert captured["attached_images"] is None
    handoff = captured["transient_model_message"]
    assert handoff.endswith(
        f"{HANDOFF_HEADER} {HANDOFF_IMAGES_INSPECT} {HANDOFF_IMAGES_FALLBACK_WITHOUT_BRIDGE} {HANDOFF_OFFICE_WITHOUT_BRIDGE}"
    )

    # The image alone: nothing about office files.
    captured.clear()
    response = await runtime_api.api_chat(_Request({"message": "", "session_id": "s1", "attachments": ["f1"]}))
    assert response.status == 200
    assert captured["message"] == "[image]"
    assert captured["transient_model_message"].endswith(
        f"{HANDOFF_HEADER} {HANDOFF_IMAGES_INSPECT} {HANDOFF_IMAGES_FALLBACK_WITHOUT_BRIDGE}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("image_analysis", [False, True])
async def test_api_chat_with_only_a_forwarded_file_still_runs_and_says_the_bridge_is_off(monkeypatch, tmp_path, image_analysis):
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    runtime_api, captured, _shot, book = _handoff_chat_fixture(
        monkeypatch, tmp_path, provider="github_copilot", bridge=False, image_analysis=image_analysis
    )

    response = await runtime_api.api_chat(_Request({"message": "", "session_id": "s1", "attachments": ["f2"]}))
    assert response.status == 200
    assert captured["message"] == "[image]"
    assert captured["attached_images"] is None
    handoff = captured["transient_model_message"]
    assert str(book) in handoff
    assert HANDOFF_WITHOUT_BRIDGE in handoff and HANDOFF_WITH_BRIDGE not in handoff
    # inspect-image reads images only: with or without the connector an office
    # file has the bridge and nothing else, and no image is waiting for a hint.
    assert handoff.endswith(f"{HANDOFF_HEADER} {HANDOFF_WITHOUT_BRIDGE}")
    assert "inspect-image" not in handoff and HANDOFF_IMAGE_ANALYSIS_HINT not in handoff


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ai_platform", "github_copilot"])
@pytest.mark.parametrize("switch", [CHAT_VISION_ENV, COPILOT_VISION_VIA_CHAT_ENV])
async def test_api_chat_inlines_images_with_the_switch_but_still_hands_off_office_files(monkeypatch, tmp_path, provider, switch):
    # No provider is sent images by default, AI Platform included: the image is handed off like the spreadsheet.
    runtime_api, captured, shot, book = _handoff_chat_fixture(monkeypatch, tmp_path, provider=provider, bridge=True, image_analysis=True)
    response = await runtime_api.api_chat(_Request({"message": "compare", "session_id": "s1", "attachments": ["f1", "f2"]}))
    assert response.status == 200
    assert captured["attached_images"] is None
    assert f"- {shot} (image/png, 3 bytes, attached as \"f1-shot.png\"; not readable by the chat model)" in captured["transient_model_message"]

    # The deployment switch (under either of its names) inlines it, for any provider.
    captured.clear()
    monkeypatch.setenv(switch, "1")
    response = await runtime_api.api_chat(_Request({"message": "compare", "session_id": "s1", "attachments": ["f1", "f2"]}))
    assert response.status == 200
    assert len(captured["attached_images"]) == 1
    assert captured["attached_images"][0].startswith("data:image/png;base64,")
    # Both files are listed with their paths; only the spreadsheet needs the analysis skill.
    handoff = captured["transient_model_message"]
    assert f"- {book} (application/vnd.ms-excel, 3 bytes, attached as \"f2-book.xls\"; not readable by the chat model)" in handoff
    assert f"- {shot} (image/png, 3 bytes, attached as \"f1-shot.png\"; sent to the model as an image)" in handoff
    assert HANDOFF_WITH_BRIDGE in handoff
    # The model saw the image itself, so nothing points at inspect-image even with the connector configured.
    assert "inspect-image" not in handoff


@pytest.mark.asyncio
async def test_api_chat_lists_a_parsed_spreadsheet_with_its_path_before_the_excerpt(monkeypatch, tmp_path):
    """Processing an xlsx takes the file: the agent gets its path, and the excerpt is marked as a preview."""
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    runtime_api, captured, shot, book = _handoff_chat_fixture(monkeypatch, tmp_path, provider="github_copilot", bridge=False)
    sheet = tmp_path / "sheet.xlsx"

    async def _context(*, session_id, attachment_ids):
        assert session_id == "s1" and attachment_ids == ["f3", "f1"]
        return {"context_file_ids": ["f3"], "image_file_ids": ["f1"], "failures": [], "parsed_file_ids": ["f3"], "already_ready_file_ids": []}

    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _context)
    monkeypatch.setattr(
        runtime_api,
        "_prepare_attachment_transient_model_message",
        lambda **kwargs: (f"EXCERPT for: {kwargs['model_context_query']}", [], "inject_context", ["f3"]),
    )

    response = await runtime_api.api_chat(_Request({"message": "turn this into a csv", "session_id": "s1", "attachments": ["f3", "f1"]}))
    assert response.status == 200
    assert captured["message"] == "turn this into a csv"
    prompt = captured["transient_model_message"]
    assert prompt.startswith(ATTACHED_FILES_HEADER)
    assert f"- {sheet} ({XLSX_MIME}, 4 bytes, attached as \"f3-sales.xlsx\"; an excerpt of it is in this prompt)" in prompt
    assert f"- {shot} (image/png, 3 bytes, attached as \"f1-shot.png\"; not readable by the chat model)" in prompt
    assert str(book) not in prompt
    # The list and the rule lead; the excerpt follows; the bridge is off for the image.
    assert prompt.index(ATTACHED_FILES_RULE) < prompt.index("EXCERPT for: turn this into a csv")
    assert HANDOFF_WITHOUT_BRIDGE in prompt and HANDOFF_WITH_BRIDGE not in prompt
    assert prompt.endswith("EXCERPT for: turn this into a csv")


@pytest.mark.asyncio
async def test_api_chat_with_only_a_parsed_file_lists_it_without_the_analysis_wording(monkeypatch, tmp_path):
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    runtime_api, captured, _shot, _book = _handoff_chat_fixture(monkeypatch, tmp_path, provider="github_copilot", bridge=False)
    sheet = tmp_path / "sheet.xlsx"

    async def _context(*, session_id, attachment_ids):
        return {"context_file_ids": ["f3"], "image_file_ids": [], "failures": [], "parsed_file_ids": ["f3"], "already_ready_file_ids": []}

    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _context)
    monkeypatch.setattr(runtime_api, "_prepare_attachment_transient_model_message", lambda **kwargs: ("EXCERPT", [], "inject_context", ["f3"]))

    response = await runtime_api.api_chat(_Request({"message": "", "session_id": "s1", "attachments": ["f3"]}))
    assert response.status == 200
    assert captured["message"] == "[attachment]"
    prompt = captured["transient_model_message"]
    assert prompt.startswith(ATTACHED_FILES_HEADER) and str(sheet) in prompt
    assert prompt.endswith("EXCERPT")
    assert HANDOFF_HEADER not in prompt and HANDOFF_WITHOUT_BRIDGE not in prompt


@pytest.mark.asyncio
async def test_api_chat_hands_a_file_that_could_not_be_parsed_to_the_agent_by_its_path(monkeypatch, tmp_path):
    """A parse failure is not a failed chat: the file is in the workspace and the agent reads it from there."""
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    runtime_api, captured, _shot, _book = _handoff_chat_fixture(monkeypatch, tmp_path, provider="github_copilot", bridge=False)
    sheet = tmp_path / "sheet.xlsx"

    async def _context(*, session_id, attachment_ids):
        return {"context_file_ids": [], "image_file_ids": [], "failures": [{"file_id": "f3", "error": "openpyxl not installed"}], "parsed_file_ids": [], "already_ready_file_ids": []}

    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _context)

    response = await runtime_api.api_chat(_Request({"message": "count the tasks per supplier", "session_id": "s1", "attachments": ["f3"]}))
    assert response.status == 200
    assert captured["message"] == "count the tasks per supplier"
    prompt = captured["transient_model_message"]
    assert prompt.startswith(ATTACHED_FILES_HEADER)
    assert f"- {sheet} ({XLSX_MIME}, 4 bytes, attached as \"f3-sales.xlsx\"; it could not be parsed, read it from its path)" in prompt
    assert "openpyxl not installed" in prompt and f"still in the workspace at {sheet}" in prompt
    assert HANDOFF_HEADER not in prompt

    # Without words of the member's own it is still an attachment turn.
    captured.clear()
    response = await runtime_api.api_chat(_Request({"message": "", "session_id": "s1", "attachments": ["f3"]}))
    assert response.status == 200
    assert captured["message"] == "[attachment]"
    assert str(sheet) in captured["transient_model_message"]


@pytest.mark.asyncio
async def test_api_chat_marks_a_parsed_file_that_retrieval_left_out_of_the_prompt(monkeypatch, tmp_path):
    """Top-k retrieval across two parsed files can cite only one: the other is not called excerpted."""
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    runtime_api, captured, _shot, _book = _handoff_chat_fixture(monkeypatch, tmp_path, provider="github_copilot", bridge=False)
    sheet = tmp_path / "sheet.xlsx"
    notes = tmp_path / "notes.csv"

    async def _context(*, session_id, attachment_ids):
        return {"context_file_ids": ["f3", "f4"], "image_file_ids": [], "failures": [], "parsed_file_ids": ["f3", "f4"], "already_ready_file_ids": []}

    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _context)
    monkeypatch.setattr(runtime_api, "_prepare_attachment_transient_model_message", lambda **kwargs: ("EXCERPT of f3", [], "inject_context", ["f3"]))

    response = await runtime_api.api_chat(_Request({"message": "compare them", "session_id": "s1", "attachments": ["f3", "f4"]}))
    assert response.status == 200
    prompt = captured["transient_model_message"]
    assert f"- {sheet} ({XLSX_MIME}, 4 bytes, attached as \"f3-sales.xlsx\"; an excerpt of it is in this prompt)" in prompt
    assert f"- {notes} (text/csv, 4 bytes, attached as \"f4-notes.csv\"; parsed, but no excerpt of it is in this prompt; read it from its path)" in prompt
    assert prompt.endswith("EXCERPT of f3")
