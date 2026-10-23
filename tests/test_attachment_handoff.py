"""Attachments the chat model cannot read are handed to the agent.

- Images reach the model as image parts only when the chat provider can see
  them (AI Platform, or Copilot with EFP_COPILOT_VISION_VIA_CHAT); otherwise
  they are listed in the handoff with their workspace paths.
- The office formats the runtime does not parse (doc, dot, xls) are always
  listed in the handoff and never parsed.
- The handoff says whether this chat can analyze the files (the Local bridge
  connector is on) or must tell the member it cannot.
"""
import asyncio

import pytest

from src.efp_runtime.llm.vision import COPILOT_VISION_VIA_CHAT_ENV
from src.gateway.attachment_handoff import (
    HANDOFF_HEADER,
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
        {"path": "/workspace/uploads/abc.png", "name": "shot.png", "content_type": "image/png", "size_bytes": 1234},
        {"path": "/workspace/uploads/def.xls", "name": "def.xls", "content_type": "application/vnd.ms-excel", "size_bytes": 99},
    ]
    text = build_attachment_handoff(files, bridge_available=True)
    assert text.startswith(HANDOFF_HEADER)
    assert '- /workspace/uploads/abc.png (image/png, 1234 bytes, attached as "shot.png")' in text
    assert "- /workspace/uploads/def.xls (application/vnd.ms-excel, 99 bytes)" in text
    assert HANDOFF_WITH_BRIDGE in text and HANDOFF_WITHOUT_BRIDGE not in text
    assert "Productivity Suite" in text and "inspect-image" not in text

    offline = build_attachment_handoff(files, bridge_available=False)
    assert HANDOFF_WITHOUT_BRIDGE in offline and HANDOFF_WITH_BRIDGE not in offline
    assert build_attachment_handoff([], bridge_available=True) == ""


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


def _handoff_chat_fixture(monkeypatch, tmp_path, *, provider, bridge: bool):
    from src.gateway import runtime_api
    from src.utils.file_parser import storage

    captured = {}
    shot = tmp_path / "shot.png"
    book = tmp_path / "book.xls"
    shot.write_bytes(b"one")
    book.write_bytes(b"two")

    class _Meta:
        def __init__(self, file_id: str, content_type: str, name: str):
            self.file_id = file_id
            self.session_id = "s1"
            self.content_type = content_type
            self.original_filename = name

    metadata_map = {"f1": _Meta("f1", "image/png", "f1-shot.png"), "f2": _Meta("f2", "application/vnd.ms-excel", "f2-book.xls")}
    file_map = {"f1": shot, "f2": book}

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

    async def json(self):
        return self._payload


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
    assert f"- {shot} (image/png, 3 bytes, attached as \"f1-shot.png\")" in handoff
    assert f"- {book} (application/vnd.ms-excel, 3 bytes, attached as \"f2-book.xls\")" in handoff
    assert HANDOFF_WITH_BRIDGE in handoff and HANDOFF_WITHOUT_BRIDGE not in handoff
    assert "inspect-image" not in handoff
    assert captured["attachments"] == ["f1", "f2"]


@pytest.mark.asyncio
async def test_api_chat_with_only_a_forwarded_file_still_runs_and_says_the_bridge_is_off(monkeypatch, tmp_path):
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    runtime_api, captured, _shot, book = _handoff_chat_fixture(monkeypatch, tmp_path, provider="github_copilot", bridge=False)

    response = await runtime_api.api_chat(_Request({"message": "", "session_id": "s1", "attachments": ["f2"]}))
    assert response.status == 200
    assert captured["message"] == "[image]"
    assert captured["attached_images"] is None
    handoff = captured["transient_model_message"]
    assert str(book) in handoff
    assert HANDOFF_WITHOUT_BRIDGE in handoff and HANDOFF_WITH_BRIDGE not in handoff


@pytest.mark.asyncio
async def test_api_chat_inlines_images_for_an_ai_platform_profile_but_still_hands_off_office_files(monkeypatch, tmp_path):
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    runtime_api, captured, shot, book = _handoff_chat_fixture(monkeypatch, tmp_path, provider="ai_platform", bridge=True)

    response = await runtime_api.api_chat(_Request({"message": "compare", "session_id": "s1", "attachments": ["f1", "f2"]}))
    assert response.status == 200
    assert len(captured["attached_images"]) == 1
    assert captured["attached_images"][0].startswith("data:image/png;base64,")
    handoff = captured["transient_model_message"]
    assert str(book) in handoff and str(shot) not in handoff
