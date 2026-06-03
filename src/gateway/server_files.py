"""API-only workspace file routes used by Portal's Server Files panel."""

from __future__ import annotations

import io
import logging
import mimetypes
import os
import shutil
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence
from urllib.parse import quote

from aiohttp import web

from src.config import config
from src.workspace_defaults import resolve_runtime_workspace


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadedPart:
    field_name: str
    filename: str
    content_type: str | None
    data: bytes


@dataclass(frozen=True)
class PreparedDownload:
    filename: str
    content_type: str | None
    file_path: Path | None = None
    data: bytes | None = None


def _runtime_workspace_root() -> Path:
    try:
        config_data = config.get_effective_config()
    except Exception:
        config_data = getattr(config, "_config", None)
    return resolve_runtime_workspace(config_data).expanduser().resolve(strict=False)


class WorkspaceServerFilesService:
    """Workspace-scoped file operations for API-only server-files routes."""

    def __init__(self, root: str | Path | None = None):
        self.root = (
            Path(root).expanduser().resolve(strict=False)
            if root is not None
            else _runtime_workspace_root()
        )

    def resolve_workspace_path(self, user_path: str | None, *, allow_missing: bool = False) -> Path:
        raw = (user_path or ".").strip()
        if raw in {"", ".", "/", self.root.as_posix(), str(self.root)}:
            return self.root

        normalized = raw.replace("\\", "/")
        candidate = Path(normalized)
        if not candidate.is_absolute():
            candidate = self.root / normalized

        lexical = self._lexical_normalize(candidate)
        self._ensure_lexically_under_workspace(lexical)
        self._reject_symlink_components(lexical, allow_missing=allow_missing)
        resolved = lexical.resolve(strict=False)
        self._ensure_under_workspace(resolved)
        return resolved

    def workspace_relative_path(self, path: Path) -> str:
        resolved = self._ensure_under_workspace(path.resolve(strict=False))
        rel = resolved.relative_to(self.root)
        return "." if str(rel) == "." else rel.as_posix()

    def list_files(self, user_path: str | None) -> dict[str, Any]:
        target = self.resolve_workspace_path(user_path)
        if not target.exists():
            raise FileNotFoundError
        if target.is_symlink():
            raise PermissionError("path outside workspace")
        if not target.is_dir():
            raise ValueError("path must be a directory")

        items: list[dict[str, Any]] = []
        for entry in target.iterdir():
            if entry.is_symlink():
                continue
            stat_result = entry.stat()
            is_dir = entry.is_dir()
            relative_path = self.workspace_relative_path(entry)
            items.append(
                {
                    "name": entry.name,
                    "path": relative_path,
                    "relative_path": relative_path,
                    "is_dir": is_dir,
                    "is_file": entry.is_file(),
                    "type": "directory" if is_dir else "file",
                    "size": 0 if is_dir else stat_result.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat_result.st_mtime,
                        timezone.utc,
                    ).isoformat(),
                }
            )

        items.sort(key=lambda item: (not item["is_dir"], str(item["name"]).lower()))
        relative_path = self.workspace_relative_path(target)
        return {
            "success": True,
            "root_path": str(self.root),
            "path": relative_path,
            "relative_path": relative_path,
            "items": items,
        }

    def read_file(self, user_path: str | None) -> dict[str, Any]:
        target = self._resolve_existing_file(user_path)
        raw = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        relative_path = self.workspace_relative_path(target)
        return {
            "success": True,
            "path": relative_path,
            "relative_path": relative_path,
            "content": raw.decode("utf-8", errors="replace"),
            "language": _guess_language(target),
            "content_type": content_type,
            "size": len(raw),
        }

    def get_content_path(self, user_path: str | None) -> Path:
        return self._resolve_existing_file(user_path)

    def upload_file(self, directory: str | None, filename: str, data: bytes) -> dict[str, Any]:
        target, name = self._resolve_write_target(directory, filename)
        target.write_bytes(data)
        relative_path = self.workspace_relative_path(target)
        return {
            "success": True,
            "mode": "file",
            "uploaded_filename": name,
            "path": relative_path,
            "relative_path": relative_path,
            "target_path": str(target),
            "size": len(data),
            "content_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
        }

    def extract_zip_safely(
        self,
        directory: str | None,
        filename: str,
        data: bytes,
    ) -> dict[str, Any]:
        uploaded_filename = _sanitize_filename(filename)
        target_dir = self._resolve_target_directory(directory)
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ValueError("invalid_zip_file") from exc

        extracted_items: list[str] = []
        with zf:
            for info in zf.infolist():
                member_name = info.filename.replace("\\", "/")
                if not member_name or member_name in {".", "/"}:
                    continue
                member_path = PurePosixPath(member_name)
                if member_path.is_absolute() or ".." in member_path.parts or "\x00" in member_name:
                    raise PermissionError("zip entry outside target directory")

                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise PermissionError("zip entry outside target directory")

                dest = self._resolve_zip_destination(target_dir, member_path)
                if info.is_dir() or member_name.endswith("/"):
                    if dest.exists() and not dest.is_dir():
                        raise ValueError("cannot overwrite file with directory")
                    dest.mkdir(parents=True, exist_ok=True)
                    self._reject_symlink_components(dest)
                    continue

                if dest.exists():
                    if dest.is_symlink():
                        raise PermissionError("path outside workspace")
                    if dest.is_dir():
                        raise ValueError("cannot overwrite directory with file")

                self._reject_symlink_components(dest.parent, allow_missing=True)
                dest.parent.mkdir(parents=True, exist_ok=True)
                self._reject_symlink_components(dest.parent)
                with zf.open(info) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
                extracted_items.append(self.workspace_relative_path(dest))

        target_relative_path = self.workspace_relative_path(target_dir)
        return {
            "success": True,
            "mode": "zip",
            "uploaded_filename": uploaded_filename,
            "path": target_relative_path,
            "relative_path": target_relative_path,
            "target_path": str(target_dir),
            "extracted_count": len(extracted_items),
            "items": sorted(extracted_items),
        }

    def delete_path(self, user_path: str | None, *, recursive: bool = False) -> dict[str, Any]:
        return self.delete_paths([user_path], recursive=recursive)

    def delete_paths(
        self,
        user_paths: Sequence[str | None],
        *,
        recursive: bool = True,
    ) -> dict[str, Any]:
        targets = self._resolve_unique_existing_targets(user_paths, allow_root=False)
        filtered_targets = self._filter_nested_targets(targets)
        deleted: list[dict[str, Any]] = []

        for target in filtered_targets:
            relative_path = self.workspace_relative_path(target)
            if target.is_dir():
                if recursive:
                    self._reject_tree_symlinks(target)
                    shutil.rmtree(target)
                else:
                    target.rmdir()
            elif target.is_file():
                target.unlink()
            else:
                raise ValueError("unsupported path")
            deleted.append(
                {
                    "path": relative_path,
                    "relative_path": relative_path,
                    "deleted": True,
                }
            )

        result: dict[str, Any] = {
            "success": True,
            "deleted_count": len(deleted),
            "items": deleted,
            "paths": [item["path"] for item in deleted],
        }
        if len(deleted) == 1:
            result.update(
                {
                    "path": deleted[0]["path"],
                    "relative_path": deleted[0]["relative_path"],
                    "deleted": True,
                }
            )
        return result

    def prepare_download(self, user_paths: Sequence[str | None]) -> PreparedDownload:
        targets = self._filter_nested_targets(
            self._resolve_unique_existing_targets(user_paths or ["."], allow_root=True)
        )
        if len(targets) == 1 and targets[0].is_file():
            target = targets[0]
            return PreparedDownload(
                filename=target.name,
                content_type=mimetypes.guess_type(target.name)[0],
                file_path=target,
            )

        archive_name = self._archive_name(targets)
        data = self._build_zip_bytes(targets)
        return PreparedDownload(
            filename=archive_name,
            content_type="application/zip",
            data=data,
        )

    def _resolve_existing_file(self, user_path: str | None) -> Path:
        target = self.resolve_workspace_path(user_path)
        if not target.exists():
            raise FileNotFoundError
        if target.is_symlink():
            raise PermissionError("path outside workspace")
        if target.is_dir():
            raise ValueError("path must be a file")
        if not target.is_file():
            raise ValueError("unsupported path")
        return target

    def _resolve_target_directory(self, directory: str | None) -> Path:
        target_dir = self.resolve_workspace_path(directory, allow_missing=True)
        if target_dir.exists():
            if target_dir.is_symlink():
                raise PermissionError("path outside workspace")
            if not target_dir.is_dir():
                raise ValueError("target path must be a directory")
        self._reject_symlink_components(target_dir, allow_missing=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_components(target_dir)
        return target_dir

    def _resolve_write_target(self, directory: str | None, filename: str) -> tuple[Path, str]:
        name = _sanitize_filename(filename)
        target_dir = self._resolve_target_directory(directory)
        target = self._lexical_normalize(target_dir / name)
        self._ensure_lexically_under_workspace(target)
        self._reject_symlink_components(target, allow_missing=True)
        if target.exists():
            if target.is_symlink():
                raise PermissionError("path outside workspace")
            if target.is_dir():
                raise ValueError("target path must be a file")
        return target, name

    def _resolve_zip_destination(self, target_dir: Path, member_path: PurePosixPath) -> Path:
        dest = self._lexical_normalize(target_dir.joinpath(*member_path.parts))
        self._ensure_lexically_under_workspace(dest)
        self._ensure_under_base(dest, target_dir, "zip entry outside target directory")
        self._reject_symlink_components(dest, allow_missing=True)
        return dest

    def _resolve_unique_existing_targets(
        self,
        user_paths: Sequence[str | None],
        *,
        allow_root: bool,
    ) -> list[Path]:
        targets: dict[Path, None] = {}
        for user_path in user_paths:
            raw = "" if user_path is None else str(user_path).strip()
            if not raw:
                continue
            target = self.resolve_workspace_path(raw)
            if target == self.root and not allow_root:
                raise PermissionError("cannot delete workspace root")
            if not target.exists():
                raise FileNotFoundError
            if target.is_symlink():
                raise PermissionError("path outside workspace")
            targets.setdefault(target, None)
        if not targets:
            raise ValueError("path is required")
        return list(targets.keys())

    def _filter_nested_targets(self, targets: Sequence[Path]) -> list[Path]:
        ordered = sorted(
            targets,
            key=lambda path: (len(path.relative_to(self.root).parts), path.as_posix()),
        )
        filtered: list[Path] = []
        for target in ordered:
            if any(_is_relative_to(target, parent) and target != parent for parent in filtered):
                continue
            filtered.append(target)
        return filtered

    def _build_zip_bytes(self, targets: Sequence[Path]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for target in targets:
                if target.is_file():
                    zf.write(target, arcname=self.workspace_relative_path(target))
                    continue
                if target.is_dir():
                    base_rel = self.workspace_relative_path(target)
                    for path in target.rglob("*"):
                        if path.is_symlink():
                            continue
                        if not path.is_file():
                            continue
                        resolved = self._ensure_under_workspace(path.resolve(strict=False))
                        child_rel = path.relative_to(target).as_posix()
                        arcname = child_rel if base_rel == "." else (PurePosixPath(base_rel) / child_rel).as_posix()
                        zf.write(resolved, arcname=arcname)
                    continue
                raise ValueError("unsupported path")
        return buf.getvalue()

    def _archive_name(self, targets: Sequence[Path]) -> str:
        if len(targets) == 1:
            target = targets[0]
            if target == self.root:
                return "workspace.zip"
            return f"{target.name or 'workspace'}.zip"
        return "server-files.zip"

    def _reject_tree_symlinks(self, target: Path) -> None:
        for path in target.rglob("*"):
            if path.is_symlink():
                raise PermissionError("path contains symlink")

    def _ensure_under_workspace(self, path: Path) -> Path:
        return self._ensure_under_base(path, self.root, "path outside workspace")

    def _ensure_under_base(self, path: Path, base: Path, message: str) -> Path:
        resolved = path.resolve(strict=False)
        base_resolved = base.resolve(strict=False)
        try:
            resolved.relative_to(base_resolved)
        except ValueError as exc:
            raise PermissionError(message) from exc
        return resolved

    def _ensure_lexically_under_workspace(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError("path outside workspace") from exc

    def _reject_symlink_components(self, path: Path, *, allow_missing: bool = False) -> None:
        try:
            rel = path.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError("path outside workspace") from exc

        current = self.root
        for part in rel.parts:
            current = current / part
            if current.is_symlink():
                raise PermissionError("path outside workspace")
            if not current.exists() and allow_missing:
                return

    def _lexical_normalize(self, path: Path) -> Path:
        return Path(os.path.normpath(os.fspath(path)))


SERVER_FILES_SERVICE_KEY = web.AppKey("efp_server_files_service", WorkspaceServerFilesService)


def _error(exc: Exception) -> web.Response:
    if isinstance(exc, PermissionError):
        return web.json_response({"success": False, "error": str(exc)}, status=403)
    if isinstance(exc, FileNotFoundError):
        return web.json_response({"success": False, "error": "not_found"}, status=404)
    if isinstance(exc, ValueError):
        msg = str(exc)
        status = 415 if msg == "unsupported_file_type" else 400
        return web.json_response({"success": False, "error": msg}, status=status)
    if isinstance(exc, OSError):
        return web.json_response({"success": False, "error": str(exc)}, status=400)
    logger.exception("Unhandled server-files error")
    return web.json_response({"success": False, "error": str(exc)}, status=500)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


async def _multipart_upload(request: web.Request) -> tuple[UploadedPart | None, dict[str, str]]:
    if not request.content_type.startswith("multipart/"):
        raise ValueError("multipart/form-data required")

    reader = await request.multipart()
    fields: dict[str, str] = {}
    upload: UploadedPart | None = None
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.filename:
            data = await part.read(decode=False)
            candidate = UploadedPart(
                field_name=part.name or "",
                filename=part.filename or "upload.bin",
                content_type=part.headers.get("Content-Type"),
                data=data,
            )
            if upload is None or candidate.field_name == "file":
                upload = candidate
            continue
        if part.name:
            fields[part.name] = await part.text()
    return upload, fields


def _sanitize_filename(filename: str) -> str:
    name = (filename or "").replace("\x00", "").replace("\r", "").replace("\n", "")
    name = name.replace("\\", "/").split("/")[-1].strip()
    if not name:
        raise ValueError("invalid filename")
    return name


def _guess_language(path: Path) -> str:
    mapping = {
        ".css": "css",
        ".csv": "csv",
        ".html": "html",
        ".js": "javascript",
        ".json": "json",
        ".jsx": "javascript",
        ".md": "markdown",
        ".py": "python",
        ".sh": "shell",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".txt": "text",
        ".yaml": "yaml",
        ".yml": "yaml",
    }
    return mapping.get(path.suffix.lower(), "text")


def _is_zip_filename(filename: str) -> bool:
    return filename.lower().endswith(".zip")


def _content_disposition(filename: str, *, disposition: str = "attachment") -> str:
    ascii_filename = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in {'"', "\\"} else "_"
        for ch in filename
    ).strip() or "download"
    return f'{disposition}; filename="{ascii_filename}"; filename*=UTF-8\'\'{quote(filename)}'


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def setup_server_files_routes(app: web.Application) -> None:
    service = app.get(SERVER_FILES_SERVICE_KEY)
    if service is None:
        service = WorkspaceServerFilesService()
        app[SERVER_FILES_SERVICE_KEY] = service

    async def server_files_browse(request: web.Request) -> web.Response:
        try:
            return web.json_response(service.list_files(request.query.get("path") or "."))
        except Exception as exc:
            return _error(exc)

    async def server_files_read(request: web.Request) -> web.Response:
        try:
            return web.json_response(service.read_file(request.query.get("path") or "."))
        except Exception as exc:
            return _error(exc)

    async def server_files_content(request: web.Request) -> web.StreamResponse:
        try:
            path = service.get_content_path(request.query.get("path") or ".")
            response = web.FileResponse(path)
            content_type = mimetypes.guess_type(path.name)[0]
            if content_type:
                response.content_type = content_type
            return response
        except Exception as exc:
            return _error(exc)

    async def server_files_upload(request: web.Request) -> web.Response:
        try:
            upload, fields = await _multipart_upload(request)
            if upload is None:
                raise ValueError("file is required")
            directory = (
                request.query.get("directory")
                or request.query.get("path")
                or fields.get("directory")
                or fields.get("path")
                or "."
            )
            unzip = _truthy(request.query.get("unzip") or fields.get("unzip"))
            if unzip or _is_zip_filename(upload.filename):
                return web.json_response(
                    service.extract_zip_safely(directory, upload.filename, upload.data)
                )
            return web.json_response(service.upload_file(directory, upload.filename, upload.data))
        except Exception as exc:
            return _error(exc)

    async def server_files_delete(request: web.Request) -> web.Response:
        try:
            payload: dict[str, Any] = {}
            if request.content_type.startswith("application/json"):
                payload = await request.json()
            elif request.content_type.startswith("multipart/") or request.content_type.startswith(
                "application/x-www-form-urlencoded"
            ):
                payload = dict(await request.post())

            raw_paths = payload.get("paths")
            if isinstance(raw_paths, list):
                return web.json_response(service.delete_paths(raw_paths, recursive=True))
            if isinstance(raw_paths, str) and raw_paths.strip():
                return web.json_response(service.delete_paths([raw_paths], recursive=True))

            path = payload.get("path") or request.query.get("path")
            if not path:
                raise ValueError("path is required")
            recursive = _truthy(str(payload.get("recursive") or request.query.get("recursive") or "false"))
            return web.json_response(service.delete_path(path, recursive=recursive))
        except Exception as exc:
            return _error(exc)

    async def server_files_download(request: web.Request) -> web.StreamResponse:
        try:
            paths = request.query.getall("paths", [])
            if not paths:
                paths = request.query.getall("paths[]", [])
            if not paths:
                paths = [request.query.get("path") or "."]

            prepared = service.prepare_download(paths)
            headers = {"Content-Disposition": _content_disposition(prepared.filename)}
            if prepared.file_path is not None:
                response = web.FileResponse(prepared.file_path, headers=headers)
                if prepared.content_type:
                    response.content_type = prepared.content_type
                return response
            return web.Response(
                body=prepared.data or b"",
                content_type=prepared.content_type or "application/octet-stream",
                headers=headers,
            )
        except Exception as exc:
            return _error(exc)

    app.router.add_get("/api/server-files", server_files_browse)
    app.router.add_get("/api/server-files/read", server_files_read)
    app.router.add_get("/api/server-files/content", server_files_content)
    app.router.add_post("/api/server-files/upload", server_files_upload)
    app.router.add_post("/api/server-files/delete", server_files_delete)
    app.router.add_get("/api/server-files/download", server_files_download)

    logger.info("Server Files API routes registered for workspace root: %s", service.root)
