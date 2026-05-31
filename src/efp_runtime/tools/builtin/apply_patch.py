"""Workspace-contained unified diff application tool for EFP runtime."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef
from .diff_preview import (
    DEFAULT_MAX_PREVIEW_CHARS,
    DEFAULT_MAX_PREVIEW_LINES,
    bounded_text_preview,
    file_diff_record,
)
from .filesystem import normalize_workspace_root, resolve_workspace_path, workspace_relative_path


def create_apply_patch_tool(
    workspace_root: str | Path,
    *,
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        patch = args["patch"]
        max_preview_lines = args.get(
            "max_patch_preview_lines",
            DEFAULT_MAX_PREVIEW_LINES,
        )
        max_preview_chars = args.get(
            "max_patch_preview_chars",
            DEFAULT_MAX_PREVIEW_CHARS,
        )
        patch_preview, patch_preview_truncated = bounded_text_preview(
            patch,
            max_preview_lines,
            max_preview_chars,
        )
        raw_paths = _extract_patch_display_paths(patch)
        call_id = context.tool_call_id or ""
        if not patch.strip():
            return _patch_error(
                "patch must not be empty.",
                call_id=call_id,
                paths=raw_paths,
                patch_preview=patch_preview,
                patch_preview_truncated=patch_preview_truncated,
                max_preview_lines=max_preview_lines,
                max_preview_chars=max_preview_chars,
            )

        try:
            paths = _validate_patch_paths(root, patch)
        except ValueError as exc:
            return _patch_error(
                str(exc),
                call_id=call_id,
                paths=raw_paths,
                patch_preview=patch_preview,
                patch_preview_truncated=patch_preview_truncated,
                max_preview_lines=max_preview_lines,
                max_preview_chars=max_preview_chars,
            )
        patch_files = _extract_patch_files(root, patch)

        check = await _run_git_apply(root, patch, check=True)
        if check["missing_git"]:
            return _patch_error(
                "git executable was not found.",
                call_id=call_id,
                stdout=check["stdout"],
                stderr=check["stderr"],
                exit_code=check["exit_code"],
                paths=paths,
                patch_preview=patch_preview,
                patch_preview_truncated=patch_preview_truncated,
                max_preview_lines=max_preview_lines,
                max_preview_chars=max_preview_chars,
            )
        if check["exit_code"] != 0:
            return _patch_error(
                "Patch check failed.",
                call_id=call_id,
                stdout=check["stdout"],
                stderr=check["stderr"],
                exit_code=check["exit_code"],
                paths=paths,
                patch_preview=patch_preview,
                patch_preview_truncated=patch_preview_truncated,
                max_preview_lines=max_preview_lines,
                max_preview_chars=max_preview_chars,
            )

        before_texts = _read_patch_text_snapshots(root, patch_files)
        applied = await _run_git_apply(root, patch, check=False)
        if applied["exit_code"] != 0:
            return _patch_error(
                "Patch apply failed.",
                call_id=call_id,
                stdout=applied["stdout"],
                stderr=applied["stderr"],
                exit_code=applied["exit_code"],
                paths=paths,
                patch_preview=patch_preview,
                patch_preview_truncated=patch_preview_truncated,
                max_preview_lines=max_preview_lines,
                max_preview_chars=max_preview_chars,
            )
        after_texts = _read_patch_text_snapshots(root, patch_files)
        filediffs = _build_file_diff_records(
            patch_files,
            before_texts=before_texts,
            after_texts=after_texts,
            max_preview_lines=max_preview_lines,
            max_preview_chars=max_preview_chars,
        )

        output = {
            "ok": True,
            "paths": paths,
            "stdout": applied["stdout"],
            "stderr": applied["stderr"],
            "exit_code": applied["exit_code"],
            "changed_file_count": len(paths),
            "patch_preview": patch_preview,
            "patch_preview_truncated": patch_preview_truncated,
            "filediffs": filediffs,
        }
        metadata = {"filediffs": filediffs}
        if len(filediffs) == 1:
            output["filediff"] = filediffs[0]
            metadata["filediff"] = filediffs[0]
        return ToolResult(
            call_id=call_id,
            tool_name="apply_patch",
            status="success",
            success=True,
            content=_format_patch_success_content(
                paths=paths,
                exit_code=applied["exit_code"],
                patch_preview=patch_preview,
                patch_preview_truncated=patch_preview_truncated,
            ),
            output=output,
            metadata=metadata,
        )

    return ToolDef(
        id="apply_patch",
        description="Apply a unified diff to files inside the workspace.",
        input_schema={
            "type": "object",
            "required": ["patch"],
            "properties": {
                "patch": {"type": "string"},
                "max_patch_preview_lines": {"type": "integer", "minimum": 0},
                "max_patch_preview_chars": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=permission
        or PermissionMetadata(
            action=ALLOW,
            category="filesystem",
            resource="workspace",
            risk="medium",
        ),
    )


@dataclass(frozen=True)
class _PatchFile:
    old_path: str | None
    path: str | None
    patch: str


def _validate_patch_paths(workspace_root: Path, patch: str) -> list[str]:
    paths = sorted(
        {
            workspace_relative_path(workspace_root, resolve_workspace_path(workspace_root, path))
            for path in _extract_patch_paths(patch)
            if path != "/dev/null"
        },
        key=lambda value: (value.casefold(), value),
    )
    return paths


def _extract_patch_files(workspace_root: Path, patch: str) -> list[_PatchFile]:
    files: list[_PatchFile] = []
    for section in _split_patch_file_sections(patch):
        old_path, path = _parse_patch_file_paths(section)
        if old_path is None and path is None:
            continue
        old_relative = _workspace_relative_patch_path(workspace_root, old_path)
        relative = _workspace_relative_patch_path(workspace_root, path)
        files.append(_PatchFile(old_path=old_relative, path=relative, patch=section))
    return files


def _split_patch_file_sections(patch: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git ") and current:
            sections.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("".join(current))
    return [section for section in sections if section.strip()]


def _parse_patch_file_paths(section: str) -> tuple[str | None, str | None]:
    old_path: str | None = None
    path: str | None = None
    for line in section.splitlines():
        if line.startswith("diff --git "):
            paths = _extract_diff_git_paths(line)
            if len(paths) >= 2:
                old_path, path = paths[0], paths[1]
        elif line.startswith("--- "):
            header_path = _parse_patch_header_path(line[4:])
            old_path = _header_workspace_path(header_path)
        elif line.startswith("+++ "):
            header_path = _parse_patch_header_path(line[4:])
            path = _header_workspace_path(header_path)
        elif line.startswith("rename from "):
            old_path = line[len("rename from ") :]
        elif line.startswith("rename to "):
            path = line[len("rename to ") :]
        elif line.startswith("copy from "):
            old_path = line[len("copy from ") :]
        elif line.startswith("copy to "):
            path = line[len("copy to ") :]
    return old_path, path


def _header_workspace_path(path: str | None) -> str | None:
    if path is None or path == "/dev/null":
        return None
    return _strip_diff_prefix(path)


def _workspace_relative_patch_path(
    workspace_root: Path,
    path: str | None,
) -> str | None:
    if path is None or path == "/dev/null":
        return None
    return workspace_relative_path(
        workspace_root,
        resolve_workspace_path(workspace_root, path),
    )


def _read_patch_text_snapshots(
    workspace_root: Path,
    patch_files: list[_PatchFile],
) -> dict[str, str | None]:
    snapshots: dict[str, str | None] = {}
    for path in _patch_snapshot_paths(patch_files):
        snapshots[path] = _read_workspace_text_snapshot(workspace_root, path)
    return snapshots


def _patch_snapshot_paths(patch_files: list[_PatchFile]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for patch_file in patch_files:
        for path in (patch_file.old_path, patch_file.path):
            if path is not None and path not in seen:
                paths.append(path)
                seen.add(path)
    return paths


def _read_workspace_text_snapshot(workspace_root: Path, relative_path: str) -> str | None:
    path = resolve_workspace_path(workspace_root, relative_path)
    if not path.exists():
        return ""
    if not path.is_file():
        return None
    data = path.read_bytes()
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _build_file_diff_records(
    patch_files: list[_PatchFile],
    *,
    before_texts: dict[str, str | None],
    after_texts: dict[str, str | None],
    max_preview_lines: int,
    max_preview_chars: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for patch_file in patch_files:
        path = patch_file.path or patch_file.old_path
        if path is None:
            continue
        old_path = patch_file.old_path or path
        old_text = before_texts.get(old_path, "")
        new_text = after_texts.get(path, "")
        if old_text is None or new_text is None:
            continue
        patch, _ = bounded_text_preview(
            patch_file.patch,
            max_preview_lines,
            max_preview_chars,
        )
        records.append(
            file_diff_record(
                path=path,
                old_path=old_path,
                old_text=old_text,
                new_text=new_text,
                patch=patch,
            )
        )
    return records


def _extract_patch_display_paths(patch: str) -> list[str]:
    paths = sorted(
        {
            _strip_diff_prefix(path)
            for path in _extract_patch_paths(patch)
            if path and path != "/dev/null"
        },
        key=lambda value: (value.casefold(), value),
    )
    return paths


def _extract_patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            paths.extend(_extract_diff_git_paths(line))
        elif line.startswith("--- "):
            path = _parse_patch_header_path(line[4:])
            if path:
                paths.append(_strip_diff_prefix(path))
        elif line.startswith("+++ "):
            path = _parse_patch_header_path(line[4:])
            if path:
                paths.append(_strip_diff_prefix(path))
        elif line.startswith("rename from "):
            paths.append(line[len("rename from ") :])
        elif line.startswith("rename to "):
            paths.append(line[len("rename to ") :])
        elif line.startswith("copy from "):
            paths.append(line[len("copy from ") :])
        elif line.startswith("copy to "):
            paths.append(line[len("copy to ") :])
    return paths


def _extract_diff_git_paths(line: str) -> list[str]:
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    if len(parts) < 4:
        return []
    return [_strip_diff_prefix(parts[2]), _strip_diff_prefix(parts[3])]


def _parse_patch_header_path(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if value == "/dev/null":
        return value
    if value.startswith('"'):
        try:
            parts = shlex.split(value)
        except ValueError:
            parts = []
        if parts:
            return parts[0]
    if "\t" in value:
        return value.split("\t", 1)[0]
    return value


def _strip_diff_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


async def _run_git_apply(root: Path, patch: str, *, check: bool) -> dict[str, Any]:
    command = ["git", "apply", "--whitespace=nowarn"]
    if check:
        command.append("--check")
    command.append("-")
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(root),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "missing_git": True,
        }

    stdout_bytes, stderr_bytes = await process.communicate(patch.encode("utf-8"))
    return {
        "stdout": stdout_bytes.decode("utf-8", errors="replace"),
        "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        "exit_code": process.returncode,
        "missing_git": False,
    }


def _patch_error(
    message: str,
    *,
    call_id: str = "",
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    paths: list[str] | None = None,
    patch_preview: str = "",
    patch_preview_truncated: bool = False,
    max_preview_lines: int = DEFAULT_MAX_PREVIEW_LINES,
    max_preview_chars: int = DEFAULT_MAX_PREVIEW_CHARS,
) -> ToolResult:
    stderr_preview, stderr_preview_truncated = bounded_text_preview(
        stderr,
        max_preview_lines,
        max_preview_chars,
    )
    stdout_preview, stdout_preview_truncated = bounded_text_preview(
        stdout,
        max_preview_lines,
        max_preview_chars,
    )
    output = {
        "ok": False,
        "error": message,
        "paths": list(paths or []),
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "stdout_preview": stdout_preview,
        "stdout_preview_truncated": stdout_preview_truncated,
        "stderr_preview": stderr_preview,
        "stderr_preview_truncated": stderr_preview_truncated,
        "patch_preview": patch_preview,
        "patch_preview_truncated": patch_preview_truncated,
    }
    return ToolResult(
        call_id=call_id,
        tool_name="apply_patch",
        status="error",
        success=False,
        error=message,
        content=_format_patch_error_content(
            message=message,
            paths=list(paths or []),
            exit_code=exit_code,
            stderr_preview=stderr_preview,
            stderr_preview_truncated=stderr_preview_truncated,
            stdout_preview=stdout_preview,
            stdout_preview_truncated=stdout_preview_truncated,
            patch_preview=patch_preview,
            patch_preview_truncated=patch_preview_truncated,
        ),
        output=output,
    )


def _format_patch_success_content(
    *,
    paths: list[str],
    exit_code: int | None,
    patch_preview: str,
    patch_preview_truncated: bool,
) -> str:
    parts = [
        "Patch applied successfully.",
        f"Changed paths: {_format_paths(paths)}",
        f"changed_file_count={len(paths)}, exit_code={exit_code}",
        "",
        "Patch preview:",
    ]
    _append_preview(
        parts,
        patch_preview,
        truncated=patch_preview_truncated,
        truncation_message="Patch preview truncated by max_patch_preview_lines/max_patch_preview_chars.",
    )
    return "\n".join(parts)


def _format_patch_error_content(
    *,
    message: str,
    paths: list[str],
    exit_code: int | None,
    stderr_preview: str,
    stderr_preview_truncated: bool,
    stdout_preview: str,
    stdout_preview_truncated: bool,
    patch_preview: str,
    patch_preview_truncated: bool,
) -> str:
    parts = [
        f"Patch failed: {message}",
        f"Paths: {_format_paths(paths)}",
        f"Exit code: {exit_code}",
        "",
        "stderr preview:",
    ]
    _append_preview(
        parts,
        stderr_preview,
        truncated=stderr_preview_truncated,
        truncation_message="stderr preview truncated.",
        fenced=False,
    )
    if stdout_preview or stdout_preview_truncated:
        parts.extend(["", "stdout preview:"])
        _append_preview(
            parts,
            stdout_preview,
            truncated=stdout_preview_truncated,
            truncation_message="stdout preview truncated.",
            fenced=False,
        )
    parts.extend(["", "Patch preview:"])
    _append_preview(
        parts,
        patch_preview,
        truncated=patch_preview_truncated,
        truncation_message="Patch preview truncated by max_patch_preview_lines/max_patch_preview_chars.",
    )
    return "\n".join(parts)


def _append_preview(
    parts: list[str],
    preview: str,
    *,
    truncated: bool,
    truncation_message: str,
    fenced: bool = True,
) -> None:
    if preview:
        if fenced:
            parts.extend(["```diff", preview.rstrip("\n"), "```"])
        else:
            parts.append(preview.rstrip("\n"))
    elif truncated:
        parts.append("(truncated to an empty preview)")
    else:
        parts.append("(empty)")
    if truncated:
        parts.append(truncation_message)


def _format_paths(paths: list[str]) -> str:
    if not paths:
        return "(none detected)"
    return ", ".join(paths)
