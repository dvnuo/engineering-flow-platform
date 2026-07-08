"""Workspace file management: move/mkdir/new-file, read_file cap, temp-zip download.

Covers the B1/B2 management operations, the A3 read cap + binary handling, and
the A1 streaming (temp-file) download for directories.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.gateway.server_files import WorkspaceServerFilesService


# --------------------------------------------------------------------------- #
# Service-level tests
# --------------------------------------------------------------------------- #

def _svc(root: Path) -> WorkspaceServerFilesService:
    return WorkspaceServerFilesService(root)


def test_move_renames_file(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    result = _svc(tmp_path).move_path("a.txt", "b.txt")
    assert result["success"] is True
    assert result["relative_path"] == "b.txt"
    assert not (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "hi"


def test_move_into_subdirectory_creates_parents(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    result = _svc(tmp_path).move_path("a.txt", "sub/deep/a.txt")
    assert result["relative_path"] == "sub/deep/a.txt"
    assert (tmp_path / "sub" / "deep" / "a.txt").exists()


def test_move_rejects_overwrite(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    with pytest.raises(ValueError):
        _svc(tmp_path).move_path("a.txt", "b.txt")
    assert (tmp_path / "a.txt").exists()  # unchanged


def test_move_rejects_missing_source(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        _svc(tmp_path).move_path("nope.txt", "x.txt")


def test_move_rejects_escape(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    with pytest.raises(PermissionError):
        _svc(tmp_path).move_path("a.txt", "../escape.txt")


def test_mkdir_creates_nested_directory(tmp_path: Path):
    result = _svc(tmp_path).make_directory("x/y/z")
    assert result["success"] is True and result["is_dir"] is True
    assert (tmp_path / "x" / "y" / "z").is_dir()


def test_mkdir_rejects_existing(tmp_path: Path):
    (tmp_path / "d").mkdir()
    with pytest.raises(ValueError):
        _svc(tmp_path).make_directory("d")


def test_new_file_creates_empty_file_with_parents(tmp_path: Path):
    result = _svc(tmp_path).create_file("notes/todo.md")
    assert result["success"] is True and result["size"] == 0
    p = tmp_path / "notes" / "todo.md"
    assert p.is_file() and p.read_bytes() == b""


def test_new_file_rejects_existing(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        _svc(tmp_path).create_file("a.txt")


def test_read_file_truncates_over_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EFP_MAX_READ_FILE_BYTES", "10")
    (tmp_path / "big.txt").write_text("x" * 25, encoding="utf-8")
    result = _svc(tmp_path).read_file("big.txt")
    assert result["truncated"] is True
    assert result["returned_bytes"] == 10
    assert result["size"] == 25
    assert result["content"] == "x" * 10
    assert result["is_binary"] is False


def test_read_file_flags_binary_and_skips_decode(tmp_path: Path):
    (tmp_path / "blob.bin").write_bytes(b"PNG\x00\x01\x02data")
    result = _svc(tmp_path).read_file("blob.bin")
    assert result["is_binary"] is True
    assert result["content"] == ""
    assert result["truncated"] is False


def test_prepare_download_single_file_is_not_temp(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    prepared = _svc(tmp_path).prepare_download(["a.txt"])
    assert prepared.is_temp is False
    assert prepared.file_path == (tmp_path / "a.txt").resolve()


def test_prepare_download_directory_writes_temp_zip(tmp_path: Path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "one.txt").write_text("1", encoding="utf-8")
    (tmp_path / "d" / "two.txt").write_text("2", encoding="utf-8")
    prepared = _svc(tmp_path).prepare_download(["d"])
    try:
        assert prepared.is_temp is True
        assert prepared.content_type == "application/zip"
        assert prepared.file_path is not None and prepared.file_path.exists()
        with zipfile.ZipFile(prepared.file_path) as zf:
            names = set(zf.namelist())
        assert {"d/one.txt", "d/two.txt"} <= names
    finally:
        if prepared.file_path is not None:
            prepared.file_path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Route-level tests (exercise handlers + temp cleanup)
# --------------------------------------------------------------------------- #

async def _client_for_workspace(workspace: Path) -> TestClient:
    from src.gateway.server_files import (
        SERVER_FILES_SERVICE_KEY,
        WorkspaceServerFilesService,
        setup_server_files_routes,
    )

    app = web.Application()
    app[SERVER_FILES_SERVICE_KEY] = WorkspaceServerFilesService(workspace)
    setup_server_files_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_mkdir_move_new_file_routes(tmp_path: Path):
    client = await _client_for_workspace(tmp_path)
    try:
        r = await client.post("/api/server-files/mkdir", json={"path": "docs"})
        assert r.status == 200 and (await r.json())["is_dir"] is True

        r = await client.post("/api/server-files/new-file", json={"path": "docs/readme.md"})
        assert r.status == 200 and (await r.json())["size"] == 0

        r = await client.post(
            "/api/server-files/move",
            json={"source": "docs/readme.md", "destination": "docs/guide.md"},
        )
        assert r.status == 200 and (await r.json())["relative_path"] == "docs/guide.md"
        assert (tmp_path / "docs" / "guide.md").exists()
        assert not (tmp_path / "docs" / "readme.md").exists()

        # Missing required fields -> 400
        r = await client.post("/api/server-files/mkdir", json={})
        assert r.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_directory_download_streams_zip_and_cleans_temp(tmp_path: Path, monkeypatch):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.txt").write_text("alpha", encoding="utf-8")

    created: list[Path] = []
    import src.gateway.server_files as sf
    real_mkstemp = sf.tempfile.mkstemp

    def _tracking_mkstemp(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        created.append(Path(name))
        return fd, name

    monkeypatch.setattr(sf.tempfile, "mkstemp", _tracking_mkstemp)

    client = await _client_for_workspace(tmp_path)
    try:
        resp = await client.get("/api/server-files/download", params={"path": "d"})
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "application/zip"
        body = await resp.read()
    finally:
        await client.close()

    # Valid zip with the expected member...
    import io as _io
    with zipfile.ZipFile(_io.BytesIO(body)) as zf:
        assert "d/a.txt" in zf.namelist()
        assert zf.read("d/a.txt") == b"alpha"

    # ...and the temp archive was created then deleted (no leak).
    assert created, "expected a temp archive to be created"
    assert all(not p.exists() for p in created), "temp archive was not cleaned up"
