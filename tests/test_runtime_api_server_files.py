from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


async def _client_for_workspace(workspace: Path) -> TestClient:
    from src.gateway.runtime_api import setup_runtime_api_routes
    from src.gateway.server_files import SERVER_FILES_SERVICE_KEY, WorkspaceServerFilesService

    app = web.Application()
    app[SERVER_FILES_SERVICE_KEY] = WorkspaceServerFilesService(workspace)
    setup_runtime_api_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_server_files_list_returns_portal_workspace_shape(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    try:
        (tmp_path / "linked-readme").symlink_to(tmp_path / "README.md")
    except OSError:
        pass

    client = await _client_for_workspace(tmp_path)
    try:
        response = await client.get("/api/server-files", params={"path": str(tmp_path.resolve())})
        assert response.status == 200
        body = await response.json()
    finally:
        await client.close()

    assert body["success"] is True
    assert body["root_path"] == str(tmp_path.resolve())
    assert body["path"] == "."
    assert body["relative_path"] == "."
    assert [item["name"] for item in body["items"]] == ["src", "README.md"]

    required_item_keys = {
        "name",
        "path",
        "relative_path",
        "is_dir",
        "is_file",
        "type",
        "size",
        "modified_at",
    }
    assert all(required_item_keys.issubset(item.keys()) for item in body["items"])
    assert body["items"][0]["type"] == "directory"
    assert body["items"][0]["is_dir"] is True
    assert body["items"][1]["type"] == "file"
    assert body["items"][1]["relative_path"] == "README.md"


@pytest.mark.asyncio
async def test_server_files_read_returns_text_content(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")

    client = await _client_for_workspace(tmp_path)
    try:
        response = await client.get("/api/server-files/read", params={"path": "notes.txt"})
        assert response.status == 200
        body = await response.json()
    finally:
        await client.close()

    assert body["success"] is True
    assert body["path"] == "notes.txt"
    assert body["relative_path"] == "notes.txt"
    assert body["content"] == "hello\n"
    assert body["language"] == "text"
    assert body["content_type"] == "text/plain"
    assert body["size"] == 6


@pytest.mark.asyncio
async def test_server_files_rejects_paths_outside_workspace(tmp_path: Path):
    outside = tmp_path.parent / f"{tmp_path.name}-secret.txt"
    outside.write_text("secret", encoding="utf-8")

    client = await _client_for_workspace(tmp_path)
    try:
        relative_escape = await client.get("/api/server-files", params={"path": "../secret"})
        absolute_escape = await client.get("/api/server-files/read", params={"path": str(outside)})
        relative_body = await relative_escape.json()
        absolute_body = await absolute_escape.json()
    finally:
        await client.close()
        outside.unlink(missing_ok=True)

    assert relative_escape.status == 403
    assert relative_body["success"] is False
    assert absolute_escape.status == 403
    assert absolute_body["success"] is False
