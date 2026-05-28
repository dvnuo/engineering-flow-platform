"""Workspace-contained shell execution tool for EFP Runtime v2."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from ...permissions import ASK, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef
from .background_shell import ShellJobManager
from .filesystem import normalize_workspace_root, resolve_workspace_path, workspace_relative_path
from .output import (
    DEFAULT_MAX_OUTPUT_CHARS,
    DEFAULT_MAX_OUTPUT_LINES,
    save_workspace_output,
    truncate_tail,
)


DEFAULT_TIMEOUT_SECONDS = 30
_CANCEL_POLL_SECONDS = 0.05


def create_shell_exec_tool(
    workspace_root: str | Path,
    *,
    permission: PermissionMetadata | None = None,
    shell_job_manager: ShellJobManager | None = None,
    enable_background: bool = False,
) -> ToolDef:
    return _create_shell_tool(
        "shell_exec",
        workspace_root,
        description="Run a shell command from a workspace-contained working directory.",
        permission=permission,
        shell_job_manager=shell_job_manager,
        enable_background=enable_background,
    )


def create_bash_tool(
    workspace_root: str | Path,
    *,
    permission: PermissionMetadata | None = None,
    shell_job_manager: ShellJobManager | None = None,
    enable_background: bool = False,
) -> ToolDef:
    return _create_shell_tool(
        "bash",
        workspace_root,
        description="Run a bash command from a workspace-contained working directory.",
        permission=permission,
        shell_job_manager=shell_job_manager,
        enable_background=enable_background,
    )


def _create_shell_tool(
    tool_id: str,
    workspace_root: str | Path,
    *,
    description: str,
    permission: PermissionMetadata | None = None,
    shell_job_manager: ShellJobManager | None = None,
    enable_background: bool = False,
) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        command = args["command"]
        description = args.get("description") or ""
        cwd = _resolve_workdir(root, args)
        if not cwd.exists():
            raise FileNotFoundError(f"Working directory does not exist: {workspace_relative_path(root, cwd)}")
        if not cwd.is_dir():
            raise NotADirectoryError(f"Working path is not a directory: {workspace_relative_path(root, cwd)}")

        cwd_relative = workspace_relative_path(root, cwd)
        background = args.get("background", False)
        if background:
            if not enable_background or shell_job_manager is None:
                raise ValueError("Background shell jobs are disabled.")
            job = await shell_job_manager.start(
                command,
                cwd,
                description=description,
            )
            tool_call_id = _result_call_id(context, tool_id)
            output = {
                "job_id": job.job_id,
                "status": job.status,
                "cwd": cwd_relative,
                "command": command,
                "description": description,
                "started_at": job.started_at,
            }
            metadata: dict[str, Any] = {
                "background": True,
                "job_id": job.job_id,
                "status": job.status,
                "cwd": cwd_relative,
                "description": description,
                "tool_call_id": tool_call_id,
            }
            run_id = _context_value(context, "run_id")
            if run_id:
                metadata["run_id"] = run_id
            return ToolResult(
                call_id=tool_call_id,
                tool_name=tool_id,
                content=_format_background_content(output),
                output=output,
                metadata=metadata,
            )

        timeout, timeout_ms = _resolve_timeout(args)
        max_output_chars = _positive_int_arg(
            args,
            "max_output_chars",
            DEFAULT_MAX_OUTPUT_CHARS,
        )
        max_output_lines = _positive_int_arg(
            args,
            "max_output_lines",
            DEFAULT_MAX_OUTPUT_LINES,
        )

        started = time.monotonic()
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes, timed_out, cancelled, exit_code = (
            await _communicate_with_timeout_and_cancel(
                process,
                timeout=timeout,
                context=context,
            )
        )
        duration_ms = int(round((time.monotonic() - started) * 1000))

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        output = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "cwd": cwd_relative,
            "duration_ms": duration_ms,
        }
        full_content = _format_shell_content(
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            cancelled=cancelled,
            timeout_ms=timeout_ms,
        )
        content, truncated = truncate_tail(
            full_content,
            max_chars=max_output_chars,
            max_lines=max_output_lines,
        )
        output_path = save_workspace_output(
            root,
            full_content,
            name_hint=_output_name_hint(context, command, cwd_relative, description),
        )
        tool_call_id = _result_call_id(context, tool_id)
        run_id = _context_value(context, "run_id")
        metadata: dict[str, Any] = {
            "description": description,
            "cwd": cwd_relative,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "duration_ms": duration_ms,
            "output_path": output_path,
            "full_output_chars": len(full_content),
            "visible_output_chars": len(content),
            "truncated": truncated,
            "stdout_chars": len(stdout),
            "stderr_chars": len(stderr),
            "timeout_ms": timeout_ms,
            "max_output_chars": max_output_chars,
            "max_output_lines": max_output_lines,
            "tool_call_id": tool_call_id,
        }
        if run_id:
            metadata["run_id"] = run_id

        return ToolResult(
            call_id=tool_call_id,
            tool_name=tool_id,
            status="cancelled" if cancelled else "success",
            success=not cancelled,
            error="Shell command cancelled." if cancelled else None,
            content=content,
            output=output,
            metadata=metadata,
            truncated=truncated,
        )

    return ToolDef(
        id=tool_id,
        description=description,
        input_schema=_shell_input_schema(),
        execute=execute,
        permission=permission
        or PermissionMetadata(
            action=ASK,
            reason="Shell execution requires approval.",
            category="shell",
            resource="workspace",
            risk="high",
            data={
                "command_preview": "",
                "description": "",
                "workdir": ".",
            },
        ),
    )


def _shell_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["command"],
        "properties": {
            "command": {"type": "string"},
            "description": {"type": "string"},
            "cwd": {"type": "string"},
            "workdir": {"type": "string"},
            "timeout": {"type": "number"},
            "timeout_ms": {"type": "number"},
            "max_output_chars": {"type": "integer"},
            "max_output_lines": {"type": "integer"},
            "background": {"type": "boolean"},
        },
        "additionalProperties": False,
    }


def _resolve_workdir(root: Path, args: dict[str, Any]) -> Path:
    cwd_value = args.get("cwd")
    workdir_value = args.get("workdir")
    cwd_provided = cwd_value not in (None, "")
    workdir_provided = workdir_value not in (None, "")

    if cwd_provided and workdir_provided:
        cwd = resolve_workspace_path(root, cwd_value)
        workdir = resolve_workspace_path(root, workdir_value)
        if cwd != workdir:
            raise ValueError("cwd and workdir must resolve to the same workspace path.")
        return cwd

    return resolve_workspace_path(root, workdir_value or cwd_value or ".")


def _resolve_timeout(args: dict[str, Any]) -> tuple[float, int]:
    if args.get("timeout_ms") is not None:
        timeout_ms = _positive_number(args["timeout_ms"], "timeout_ms")
        return timeout_ms / 1000.0, max(1, int(round(timeout_ms)))

    timeout = _positive_number(args.get("timeout", DEFAULT_TIMEOUT_SECONDS), "timeout")
    return timeout, max(1, int(round(timeout * 1000)))


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number.")
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0.")
    return float(value)


def _positive_int_arg(args: dict[str, Any], name: str, default: int) -> int:
    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0.")
    return value


async def _communicate_with_timeout_and_cancel(
    process: asyncio.subprocess.Process,
    *,
    timeout: float,
    context: ToolContext,
) -> tuple[bytes, bytes, bool, bool, int | None]:
    communicate_task = asyncio.create_task(process.communicate())
    timeout_task = asyncio.create_task(asyncio.sleep(timeout))
    cancel_task = asyncio.create_task(_wait_for_cancel(context))
    tasks = {communicate_task, timeout_task, cancel_task}

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    if communicate_task in done:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        stdout_bytes, stderr_bytes = communicate_task.result()
        return stdout_bytes, stderr_bytes, False, False, process.returncode

    cancelled = cancel_task in done and cancel_task.result()
    timed_out = not cancelled
    for task in (timeout_task, cancel_task):
        if task is not communicate_task and not task.done():
            task.cancel()

    _kill_process(process)
    stdout_bytes, stderr_bytes = await communicate_task
    await asyncio.gather(
        *(task for task in (timeout_task, cancel_task) if task is not communicate_task),
        return_exceptions=True,
    )
    return stdout_bytes, stderr_bytes, timed_out, cancelled, None


async def _wait_for_cancel(context: ToolContext) -> bool:
    while True:
        if await context.is_cancelled():
            return True
        await asyncio.sleep(_CANCEL_POLL_SECONDS)


def _kill_process(process: asyncio.subprocess.Process) -> None:
    try:
        process.kill()
    except ProcessLookupError:
        pass


def _format_shell_content(
    *,
    stdout: str,
    stderr: str,
    timed_out: bool,
    cancelled: bool,
    timeout_ms: int,
) -> str:
    parts: list[str] = []
    if stdout:
        parts.append(_tagged_output("stdout", stdout))
    if stderr:
        parts.append(_tagged_output("stderr", stderr))
    if not parts:
        parts.append("(no output)")
    if timed_out:
        parts.append(
            "\n".join(
                [
                    "<shell_metadata>",
                    f"shell tool terminated command after exceeding timeout {timeout_ms}ms.",
                    "</shell_metadata>",
                ]
            )
        )
    if cancelled:
        parts.append(
            "\n".join(
                [
                    "<shell_metadata>",
                    "User aborted the command",
                    "</shell_metadata>",
                ]
            )
        )
    return "\n".join(parts)


def _format_background_content(output: dict[str, Any]) -> str:
    return "\n".join(
        [
            "<shell_job>",
            f"job_id: {output['job_id']}",
            f"status: {output['status']}",
            f"cwd: {output['cwd']}",
            f"command: {output['command']}",
            "</shell_job>",
        ]
    )


def _tagged_output(tag: str, content: str) -> str:
    if content.endswith("\n"):
        return f"<{tag}>\n{content}</{tag}>"
    return f"<{tag}>\n{content}\n</{tag}>"


def _result_call_id(context: ToolContext, fallback: str) -> str:
    call_id = _context_value(context, "tool_call_id")
    if call_id:
        return call_id
    if context.request_id:
        return str(context.request_id)
    return fallback


def _output_name_hint(
    context: ToolContext,
    command: str,
    cwd: str,
    description: str,
) -> str:
    call_id = _context_value(context, "tool_call_id")
    if call_id:
        return call_id

    payload = json.dumps(
        {
            "command": command,
            "cwd": cwd,
            "description": description,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"shell-{digest}"


def _context_value(context: ToolContext, key: str) -> str | None:
    value = getattr(context, key, None)
    if (value is None or value == "") and isinstance(context.metadata, dict):
        value = context.metadata.get(key)
    if value is None or value == "":
        return None
    return str(value)
