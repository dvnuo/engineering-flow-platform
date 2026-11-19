"""Workspace-contained shell execution tool for EFP runtime."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import time
from typing import Any

from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef
from .filesystem import normalize_workspace_root, resolve_workspace_path, workspace_relative_path
from .memory_guard import (
    COMMAND_SHARE_FLOOR_PERCENT,
    guard_percent,
    read_container_memory,
    session_memory_bytes,
)
from .output import (
    DEFAULT_MAX_OUTPUT_CHARS,
    DEFAULT_MAX_OUTPUT_LINES,
    save_workspace_output,
    truncate_tail,
)
from .redaction import redact_tool_output


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 30_000
_CANCEL_POLL_SECONDS = 0.05
_POSIX = os.name == "posix"

# How much of each stream (stdout, stderr) the tool holds for one command. A
# command that writes more keeps its first and last part; the middle is counted
# and dropped. The runtime shares its memory limit with the command, and what
# it captures is copied several times on the way to the model and the saved
# log, so an unbounded capture of a chatty command (a repository scan printing
# every file) could take the whole assistant down. Every command that stays
# under the limit is captured exactly as before.
DEFAULT_CAPTURE_MAX_BYTES = 8 * 1024 * 1024
CAPTURE_MAX_BYTES_ENV = "EFP_SHELL_CAPTURE_MAX_BYTES"
_CAPTURE_MIN_BYTES = 1024
_CAPTURE_HEAD_MAX_BYTES = 1024 * 1024
_CAPTURE_READ_BYTES = 64 * 1024
# A cut prefers a line boundary when one is this close to it.
_CAPTURE_LINE_SEARCH_BYTES = 64 * 1024
# The structured copy of stdout/stderr that is stored with the session (twice:
# on the tool call's state and on its result) and rewritten with every later
# message. The model-visible text and the saved log do not depend on it.
STATE_OUTPUT_MAX_CHARS = 64_000
_MEMORY_POLL_SECONDS = 0.5
_BYTES_PER_MIB = 1024 * 1024
# What a process killed with SIGKILL reports: the signal itself, or 128 + 9
# when the shell relays the status of the command it ran.
_SIGKILL_EXIT_CODES = frozenset({-9, 137})
# Commands the tool is running right now, by process id (which is also their
# session id); the memory guard picks the largest of them.
_RUNNING_COMMANDS: set[int] = set()


def create_bash_tool(
    workspace_root: str | Path,
    *,
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    return _create_shell_tool(
        "bash",
        workspace_root,
        description="Run a bash command from a workspace-contained working directory.",
        permission=permission,
    )


def _create_shell_tool(
    tool_id: str,
    workspace_root: str | Path,
    *,
    description: str,
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        command = args["command"]
        description = args["description"]
        cwd = _resolve_workdir(root, args)
        if not cwd.exists():
            raise FileNotFoundError(f"Working directory does not exist: {workspace_relative_path(root, cwd)}")
        if not cwd.is_dir():
            raise NotADirectoryError(f"Working path is not a directory: {workspace_relative_path(root, cwd)}")

        cwd_relative = workspace_relative_path(root, cwd)
        timeout, timeout_ms = _resolve_timeout(args)
        max_output_chars = DEFAULT_MAX_OUTPUT_CHARS
        max_output_lines = DEFAULT_MAX_OUTPUT_LINES

        started = time.monotonic()
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Its own process group on POSIX, so a stop or a timeout kills
            # the whole pipeline the shell started, not only the shell.
            start_new_session=_POSIX,
        )
        outcome = await _communicate_with_timeout_and_cancel(
            process,
            timeout=timeout,
            context=context,
        )
        timed_out = outcome.timed_out
        cancelled = outcome.cancelled
        exit_code = outcome.exit_code
        memory_stop = outcome.memory_stop
        duration_ms = int(round((time.monotonic() - started) * 1000))
        stdout_raw, stdout_omitted_bytes = outcome.stdout.text("stdout")
        stderr_raw, stderr_omitted_bytes = outcome.stderr.text("stderr")

        # Redact before anything downstream sees the text: the model-visible
        # content, the structured output, and the archived full log all derive
        # from these two strings. Troubleshooting CLIs (aws, kubectl, pgsql)
        # can echo credentials that must never reach the transcript.
        # Off the event loop: redaction is linear but a large `kubectl logs`
        # is megabytes, and every other session in the pod shares this loop.
        stdout, stderr = await asyncio.gather(
            asyncio.to_thread(redact_tool_output, stdout_raw),
            asyncio.to_thread(redact_tool_output, stderr_raw),
        )
        del stdout_raw, stderr_raw
        state_stdout, stdout_state_cut = _bounded_text(stdout, STATE_OUTPUT_MAX_CHARS)
        state_stderr, stderr_state_cut = _bounded_text(stderr, STATE_OUTPUT_MAX_CHARS)
        output = {
            "stdout": state_stdout,
            "stderr": state_stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "cwd": cwd_relative,
            "duration_ms": duration_ms,
        }
        if stdout_state_cut or stdout_omitted_bytes:
            output["stdout_truncated"] = True
        if stderr_state_cut or stderr_omitted_bytes:
            output["stderr_truncated"] = True
        if memory_stop is not None:
            output["memory_exceeded"] = True
        killed_by_system = (
            not timed_out
            and not cancelled
            and memory_stop is None
            and _POSIX
            and exit_code in _SIGKILL_EXIT_CODES
        )
        full_content = _format_shell_content(
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            cancelled=cancelled,
            timeout_ms=timeout_ms,
            memory_stop=memory_stop,
            killed_by_system=killed_by_system,
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
            "stdout_bytes": outcome.stdout.total_bytes,
            "stderr_bytes": outcome.stderr.total_bytes,
            "capture_omitted_bytes": stdout_omitted_bytes + stderr_omitted_bytes,
            "capture_max_bytes": outcome.stdout.max_bytes,
            "timeout_ms": timeout_ms,
            "max_output_chars": max_output_chars,
            "max_output_lines": max_output_lines,
            "tool_call_id": tool_call_id,
        }
        if memory_stop is not None:
            metadata["memory_exceeded"] = True
            metadata["memory_limit_bytes"] = memory_stop.limit_bytes
            metadata["memory_used_bytes"] = memory_stop.used_bytes
            metadata["command_memory_bytes"] = memory_stop.command_bytes
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
            action=ALLOW,
            category="shell",
            resource="workspace",
            risk="high",
            data={
                "command_preview": "",
                "description": "",
                "workdir": ".",
                # The command string is the permission subject, so a profile
                # can express declarative guardrails with subject globs:
                # tool_permissions.bash = {"*kubectl*delete*": "deny", "*": "allow"}.
                "subject_arg": "command",
            },
        ),
    )


def _shell_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["command", "description"],
        "properties": {
            "command": {"type": "string"},
            "description": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1},
            "workdir": {"type": "string"},
        },
        "additionalProperties": False,
    }


def _resolve_workdir(root: Path, args: dict[str, Any]) -> Path:
    return resolve_workspace_path(root, args.get("workdir") or ".")


def _resolve_timeout(args: dict[str, Any]) -> tuple[float, int]:
    timeout_ms = _positive_int(args.get("timeout", DEFAULT_TIMEOUT_MS), "timeout")
    return timeout_ms / 1000.0, timeout_ms


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0.")
    return value


class _StreamCapture:
    """The first and the last part of one output stream, and a count of the rest.

    Until a command writes more than ``max_bytes`` nothing is dropped and
    ``text`` returns exactly what the command wrote.
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._head_limit = min(max_bytes // 4, _CAPTURE_HEAD_MAX_BYTES)
        self._tail_limit = max_bytes - self._head_limit
        self._head = bytearray()
        self._tail: deque[bytes] = deque()
        self._tail_bytes = 0
        self.total_bytes = 0

    def feed(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        room = self._head_limit - len(self._head)
        if room > 0:
            self._head += chunk[:room]
            chunk = chunk[room:]
            if not chunk:
                return
        self._tail.append(chunk)
        self._tail_bytes += len(chunk)
        # Whole chunks leave from the front once the ones after them cover the
        # tail on their own, which cannot happen before the limit is passed.
        while self._tail_bytes - len(self._tail[0]) >= self._tail_limit:
            self._tail_bytes -= len(self._tail.popleft())

    def text(self, stream_name: str) -> tuple[str, int]:
        """The captured text and how many bytes of the stream it leaves out."""

        head = bytes(self._head)
        tail = b"".join(self._tail)
        if len(tail) > self._tail_limit:
            tail = tail[-self._tail_limit :]
        if self.total_bytes == len(head) + len(tail):
            return (head + tail).decode("utf-8", errors="replace"), 0

        head = _cut_back_to_line_end(head)
        tail = _cut_forward_to_line_start(tail)
        omitted = self.total_bytes - len(head) - len(tail)
        marker = (
            f"[{omitted} bytes left out here: the command wrote {self.total_bytes} bytes to "
            f"{stream_name} and the shell tool keeps the first {len(head)} and the last "
            f"{len(tail)}. Redirect the output to a file to keep all of it.]"
        )
        head_text = head.decode("utf-8", errors="replace")
        if head_text and not head_text.endswith("\n"):
            head_text += "\n"
        return f"{head_text}{marker}\n{tail.decode('utf-8', errors='replace')}", omitted


def _cut_back_to_line_end(data: bytes) -> bytes:
    index = data.rfind(b"\n", max(len(data) - _CAPTURE_LINE_SEARCH_BYTES, 0))
    return data if index < 0 else data[: index + 1]


def _cut_forward_to_line_start(data: bytes) -> bytes:
    index = data.find(b"\n", 0, _CAPTURE_LINE_SEARCH_BYTES)
    if index < 0 or index + 1 >= len(data):
        return data
    return data[index + 1 :]


def _capture_max_bytes() -> int:
    raw = os.environ.get(CAPTURE_MAX_BYTES_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_CAPTURE_MAX_BYTES
    try:
        value = int(raw.strip())
    except ValueError:
        return DEFAULT_CAPTURE_MAX_BYTES
    if value <= 0:
        return DEFAULT_CAPTURE_MAX_BYTES
    return max(value, _CAPTURE_MIN_BYTES)


def _bounded_text(text: str, max_chars: int) -> tuple[str, bool]:
    """``text`` when it fits, otherwise its first and last part around a one-line note."""

    if len(text) <= max_chars:
        return text, False
    head_chars = max_chars // 4
    tail_chars = max_chars - head_chars
    note = (
        f"[{len(text) - max_chars} characters left out of this record; "
        "the saved output file has them.]"
    )
    return f"{text[:head_chars]}\n{note}\n{text[-tail_chars:]}", True


@dataclass(frozen=True)
class _MemoryStop:
    """Why the memory guard stopped a command."""

    limit_bytes: int
    used_bytes: int
    command_bytes: int


@dataclass
class _CommandOutcome:
    stdout: _StreamCapture
    stderr: _StreamCapture
    timed_out: bool = False
    cancelled: bool = False
    exit_code: int | None = None
    memory_stop: _MemoryStop | None = None


async def _communicate_with_timeout_and_cancel(
    process: asyncio.subprocess.Process,
    *,
    timeout: float,
    context: ToolContext,
) -> _CommandOutcome:
    max_bytes = _capture_max_bytes()
    outcome = _CommandOutcome(
        stdout=_StreamCapture(max_bytes),
        stderr=_StreamCapture(max_bytes),
    )
    communicate_task = asyncio.create_task(
        _drain_process(process, outcome.stdout, outcome.stderr)
    )
    timeout_task = asyncio.create_task(asyncio.sleep(timeout))
    cancel_task = asyncio.create_task(_wait_for_cancel(context))
    memory_task = asyncio.create_task(_wait_for_memory_pressure(process))
    helper_tasks = (timeout_task, cancel_task, memory_task)
    tasks = {communicate_task, *helper_tasks}

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        # The run's task was cancelled outright (a forced stop, or the
        # process shutting down). ``asyncio.wait`` does not cancel what it
        # waits on, so without this the command kept running to completion
        # with nobody reading its output. Kill it and release the helpers.
        _kill_process(process)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    if communicate_task in done:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        communicate_task.result()
        outcome.exit_code = process.returncode
        return outcome

    cancelled = cancel_task in done and cancel_task.result()
    memory_stop = None
    if not cancelled and memory_task in done:
        memory_stop = memory_task.result()
    for task in helper_tasks:
        if not task.done():
            task.cancel()

    _kill_process(process)
    await communicate_task
    await asyncio.gather(*helper_tasks, return_exceptions=True)
    outcome.cancelled = bool(cancelled)
    outcome.memory_stop = memory_stop
    outcome.timed_out = not cancelled and memory_stop is None
    return outcome


async def _drain_process(
    process: asyncio.subprocess.Process,
    stdout: _StreamCapture,
    stderr: _StreamCapture,
) -> None:
    """Read both streams to their end, then wait for the command to exit."""

    await asyncio.gather(
        _capture_stream(process.stdout, stdout),
        _capture_stream(process.stderr, stderr),
    )
    await process.wait()


async def _capture_stream(
    stream: asyncio.StreamReader | None,
    capture: _StreamCapture,
) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.read(_CAPTURE_READ_BYTES)
        if not chunk:
            return
        capture.feed(chunk)


async def _wait_for_cancel(context: ToolContext) -> bool:
    while True:
        if await context.is_cancelled():
            return True
        await asyncio.sleep(_CANCEL_POLL_SECONDS)


async def _wait_for_memory_pressure(process: asyncio.subprocess.Process) -> _MemoryStop:
    """Return once the container is nearly out of memory and this command is the large one.

    Never returns where there is nothing to guard: outside a memory-limited
    container, with the guard switched off, or after a reading fails.
    """

    percent = guard_percent()
    pid = process.pid
    try:
        guarded = percent > 0 and read_container_memory() is not None
    except Exception:  # noqa: BLE001 - a guard that cannot read must never fail the command.
        logger.debug("shell.memory_guard.unavailable", exc_info=True)
        guarded = False
    if not guarded:
        await asyncio.Event().wait()

    _RUNNING_COMMANDS.add(pid)
    try:
        while True:
            await asyncio.sleep(_MEMORY_POLL_SECONDS)
            try:
                stop = _memory_stop_for(pid, percent)
            except Exception:  # noqa: BLE001 - same: stop guarding, keep the command.
                logger.debug("shell.memory_guard.read_failed pid=%s", pid, exc_info=True)
                await asyncio.Event().wait()
            if stop is not None:
                logger.warning(
                    "shell.memory_guard.stopped pid=%s used_bytes=%s limit_bytes=%s command_bytes=%s",
                    pid,
                    stop.used_bytes,
                    stop.limit_bytes,
                    stop.command_bytes,
                )
                return stop
    finally:
        _RUNNING_COMMANDS.discard(pid)


def _memory_stop_for(pid: int, percent: int) -> _MemoryStop | None:
    reading = read_container_memory()
    if reading is None or reading.used_percent < percent:
        return None
    sizes = session_memory_bytes(tuple(_RUNNING_COMMANDS))
    own = sizes.get(pid, 0)
    if own * 100 < reading.limit_bytes * COMMAND_SHARE_FLOOR_PERCENT:
        return None
    # One command per reading: the largest goes, and the others are looked at
    # again on their next poll, by which time its memory has been returned.
    largest = max(sizes, key=lambda other: (sizes[other], -other))
    if largest != pid:
        return None
    return _MemoryStop(
        limit_bytes=reading.limit_bytes,
        used_bytes=reading.used_bytes,
        command_bytes=own,
    )


def _kill_process(process: asyncio.subprocess.Process) -> None:
    if _POSIX:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
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
    memory_stop: _MemoryStop | None = None,
    killed_by_system: bool = False,
) -> str:
    parts: list[str] = []
    if stdout:
        parts.append(_tagged_output("stdout", stdout))
    if stderr:
        parts.append(_tagged_output("stderr", stderr))
    if not parts:
        parts.append("(no output)")
    if memory_stop is not None:
        parts.append(
            "\n".join(
                [
                    "<shell_metadata>",
                    "shell tool stopped this command before the assistant ran out of memory: "
                    f"{_mib(memory_stop.used_bytes)} MiB of its {_mib(memory_stop.limit_bytes)} MiB "
                    f"memory limit were in use, {_mib(memory_stop.command_bytes)} MiB of them by this "
                    "command. Only this command was stopped. "
                    f"{_LOWER_MEMORY_ADVICE}",
                    "</shell_metadata>",
                ]
            )
        )
    if killed_by_system:
        parts.append(
            "\n".join(
                [
                    "<shell_metadata>",
                    "The operating system killed this command (SIGKILL, exit code 137). The usual "
                    "cause is that it needed more memory than the assistant has. "
                    f"{_LOWER_MEMORY_ADVICE}",
                    "</shell_metadata>",
                ]
            )
        )
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


_LOWER_MEMORY_ADVICE = (
    "Run it again in a way that needs less memory: work through the input in smaller "
    "batches, read files as a stream instead of loading them whole, or use fewer parallel workers."
)


def _mib(value: int) -> int:
    return int(round(value / _BYTES_PER_MIB))


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
