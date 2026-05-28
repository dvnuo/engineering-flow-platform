"""Workspace-contained unified diff application tool for EFP Runtime v2."""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path
from typing import Any

from ...permissions import ASK, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef
from .filesystem import normalize_workspace_root, resolve_workspace_path, workspace_relative_path


def create_apply_patch_tool(
    workspace_root: str | Path,
    *,
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> dict[str, Any] | ToolResult:
        patch = args["patch"]
        if not patch.strip():
            return _patch_error("patch must not be empty.")

        try:
            paths = _validate_patch_paths(root, patch)
        except ValueError as exc:
            return _patch_error(str(exc))

        check = await _run_git_apply(root, patch, check=True)
        if check["missing_git"]:
            return _patch_error(
                "git executable was not found.",
                stdout=check["stdout"],
                stderr=check["stderr"],
                exit_code=check["exit_code"],
                paths=paths,
            )
        if check["exit_code"] != 0:
            return _patch_error(
                "Patch check failed.",
                stdout=check["stdout"],
                stderr=check["stderr"],
                exit_code=check["exit_code"],
                paths=paths,
            )

        applied = await _run_git_apply(root, patch, check=False)
        if applied["exit_code"] != 0:
            return _patch_error(
                "Patch apply failed.",
                stdout=applied["stdout"],
                stderr=applied["stderr"],
                exit_code=applied["exit_code"],
                paths=paths,
            )

        return {
            "ok": True,
            "paths": paths,
            "stdout": applied["stdout"],
            "stderr": applied["stderr"],
            "exit_code": applied["exit_code"],
        }

    return ToolDef(
        id="apply_patch",
        description="Apply a unified diff to files inside the workspace.",
        input_schema={
            "type": "object",
            "required": ["patch"],
            "properties": {
                "patch": {"type": "string"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=permission
        or PermissionMetadata(
            action=ASK,
            reason="Applying patches requires approval.",
            category="filesystem",
            resource="workspace",
            risk="medium",
        ),
    )


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
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    paths: list[str] | None = None,
) -> ToolResult:
    return ToolResult(
        call_id="",
        tool_name="apply_patch",
        status="error",
        success=False,
        error=message,
        output={
            "ok": False,
            "error": message,
            "paths": list(paths or []),
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
        },
    )
