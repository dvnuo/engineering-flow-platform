import io
import json
import zipfile
from pathlib import Path

import pytest
from aiohttp import web
from multidict import MultiDict, MultiDictProxy

from src.gateway import webchat


def test_server_files_routes_registered():
    app = web.Application()
    webchat.setup_webchat_routes(app)
    routes = [r.resource.canonical for r in app.router.routes() if r.resource]
    assert "/api/server-files" in routes
    assert "/api/server-files/read" in routes
    assert "/api/server-files/content" in routes
    assert "/api/server-files/upload" in routes
    assert "/api/server-files/download" in routes


class _Request:
    def __init__(self, query=None, multipart_reader=None):
        self.query = MultiDictProxy(MultiDict(query or {}))
        self._multipart_reader = multipart_reader

    async def multipart(self):
        return self._multipart_reader


class _Field:
    def __init__(self, name, value=None, filename=None, data=None):
        self.name = name
        self._value = value
        self.filename = filename
        self._data = data or b""

    async def text(self):
        return self._value or ""

    async def read(self, decode=False):
        return self._data


class _Multipart:
    def __init__(self, fields):
        self._fields = list(fields)
        self._idx = 0

    async def next(self):
        if self._idx >= len(self._fields):
            return None
        field = self._fields[self._idx]
        self._idx += 1
        return field


def _patch_workspace_home(monkeypatch, tmp_path):
    monkeypatch.setattr(webchat.Path, "home", classmethod(lambda cls: tmp_path))
    workspace_root = tmp_path / ".efp" / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    return workspace_root


@pytest.mark.asyncio
async def test_server_files_browse_defaults_to_workspace_root(monkeypatch, tmp_path):
    workspace_root = _patch_workspace_home(monkeypatch, tmp_path)
    (workspace_root / "a.txt").write_text("hello", encoding="utf-8")

    response = await webchat.api_server_files_browse(_Request())
    body = json.loads(response.body)

    assert response.status == 200
    assert body["root_path"] == str(workspace_root.resolve())
    assert body["path"] == str(workspace_root.resolve())
    assert body["items"][0]["name"] == "a.txt"


@pytest.mark.asyncio
async def test_server_files_rejects_outside_root_path(monkeypatch, tmp_path):
    _patch_workspace_home(monkeypatch, tmp_path)
    outside = tmp_path.parent

    response = await webchat.api_server_files_browse(_Request({"path": str(outside)}))
    body = json.loads(response.body)

    assert response.status == 400
    assert "within workspace root" in body["error"]


@pytest.mark.asyncio
async def test_server_files_read_text_utf8(monkeypatch, tmp_path):
    workspace_root = _patch_workspace_home(monkeypatch, tmp_path)
    text_file = workspace_root / "notes.md"
    text_file.write_text("hello world", encoding="utf-8")

    response = await webchat.api_server_files_read(_Request({"path": str(text_file)}))
    body = json.loads(response.body)

    assert response.status == 200
    assert body["content"] == "hello world"
    assert body["language"] == "markdown"
    assert body["content_type"] == "text/markdown"


@pytest.mark.asyncio
async def test_server_files_binary_read_fails_but_content_works(monkeypatch, tmp_path):
    workspace_root = _patch_workspace_home(monkeypatch, tmp_path)
    binary_file = workspace_root / "image.bin"
    binary_file.write_bytes(b"\xff\xd8\xff")

    read_response = await webchat.api_server_files_read(_Request({"path": str(binary_file)}))
    read_body = json.loads(read_response.body)
    assert read_response.status == 400
    assert "Cannot read binary file" in read_body["error"]

    content_response = await webchat.api_server_files_content(_Request({"path": str(binary_file)}))
    assert isinstance(content_response, web.FileResponse)
    assert content_response.headers["Content-Disposition"] == 'inline; filename="image.bin"'


@pytest.mark.asyncio
async def test_server_files_upload_regular_file(monkeypatch, tmp_path):
    workspace_root = _patch_workspace_home(monkeypatch, tmp_path)
    target_dir = workspace_root / "uploads"
    target_dir.mkdir(parents=True)

    multipart = _Multipart([
        _Field("path", value=str(target_dir)),
        _Field("file", filename="hello.txt", data=b"hello"),
    ])
    response = await webchat.api_server_files_upload(_Request(multipart_reader=multipart))
    body = json.loads(response.body)

    assert response.status == 200
    assert body["mode"] == "file_save"
    assert (target_dir / "hello.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_server_files_zip_upload_extracts_and_blocks_zip_slip(monkeypatch, tmp_path):
    workspace_root = _patch_workspace_home(monkeypatch, tmp_path)
    target_dir = workspace_root / "zips"
    target_dir.mkdir(parents=True)

    good_zip = io.BytesIO()
    with zipfile.ZipFile(good_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("nested/ok.txt", "ok")
    multipart = _Multipart([
        _Field("path", value=str(target_dir)),
        _Field("file", filename="ok.zip", data=good_zip.getvalue()),
    ])
    ok_response = await webchat.api_server_files_upload(_Request(multipart_reader=multipart))
    ok_body = json.loads(ok_response.body)
    assert ok_response.status == 200
    assert ok_body["mode"] == "zip_extract"
    assert (target_dir / "nested" / "ok.txt").exists()

    bad_zip = io.BytesIO()
    with zipfile.ZipFile(bad_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../escape.txt", "escape")
    bad_multipart = _Multipart([
        _Field("path", value=str(target_dir)),
        _Field("file", filename="bad.zip", data=bad_zip.getvalue()),
    ])
    bad_response = await webchat.api_server_files_upload(_Request(multipart_reader=bad_multipart))
    bad_body = json.loads(bad_response.body)
    assert bad_response.status == 400
    assert "Unsafe ZIP entry" in bad_body["error"]


@pytest.mark.asyncio
async def test_server_files_download_single_file(monkeypatch, tmp_path):
    workspace_root = _patch_workspace_home(monkeypatch, tmp_path)
    file_path = workspace_root / "single.txt"
    file_path.write_text("one", encoding="utf-8")

    response = await webchat.api_server_files_download(_Request({"path": str(file_path)}))

    assert isinstance(response, web.FileResponse)
    assert response.headers["Content-Disposition"] == 'attachment; filename="single.txt"'


@pytest.mark.asyncio
async def test_server_files_download_directory_as_zip(monkeypatch, tmp_path):
    workspace_root = _patch_workspace_home(monkeypatch, tmp_path)
    dir_path = workspace_root / "bundle"
    dir_path.mkdir(parents=True)
    (dir_path / "a.txt").write_text("A", encoding="utf-8")

    response = await webchat.api_server_files_download(_Request({"path": str(dir_path)}))

    assert response.status == 200
    assert response.content_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.body), "r") as archive:
        names = archive.namelist()
    assert "bundle/a.txt" in names
