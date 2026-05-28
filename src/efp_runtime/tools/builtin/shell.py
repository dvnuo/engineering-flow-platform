"""Workspace-contained shell execution tool for EFP Runtime v2."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ...permissions import ASK, PermissionMetadata
from ..definition import ToolContext, ToolDef
from .filesystem import normalize_workspace_root, resolve_workspace_path, workspace_relative_path


def create_shell_exec_tool(
    workspace_root: str | Path,
    *,
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        command = args["command"]
        cwd = resolve_workspace_path(root, args.get("cwd") or ".")
        if not cwd.exists():
            raise FileNotFoundError(f"Working directory does not exist: {workspace_relative_path(root, cwd)}")
        if not cwd.is_dir():
            raise NotADirectoryError(f"Working path is not a directory: {workspace_relative_path(root, cwd)}")

        timeout = args.get("timeout", 30)
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0.")

        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
            exit_code = process.returncode
        except TimeoutError:
            timed_out = True
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            exit_code = None

        return {
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "cwd": workspace_relative_path(root, cwd),
        }

    return ToolDef(
        id="shell_exec",
        description="Run a shell command from a workspace-contained working directory.",
        input_schema={
            "type": "object",
            "required": ["command"],
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout": {"type": "number"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=permission
        or PermissionMetadata(
            action=ASK,
            reason="Shell execution requires approval.",
            category="shell",
            resource="workspace",
            risk="high",
        ),
    )
