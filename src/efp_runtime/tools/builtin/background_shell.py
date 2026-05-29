"""In-process background shell job support for Runtime v2."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import os
from pathlib import Path
import signal
import time
from typing import Any, Mapping

from ...types import new_id, utc_now_iso


DEFAULT_MAX_BUFFER_BYTES = 1024 * 1024
_READ_CHUNK_BYTES = 8192
_KILL_GRACE_SECONDS = 2.0


@dataclass
class ShellJob:
    """A shell process managed for one ToolRuntime lifecycle."""

    job_id: str
    command: str
    cwd: str
    description: str
    started_at: str
    returncode: int | None = None
    status: str = "running"
    _process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    _stdout: "_StreamBuffer" | None = field(default=None, repr=False)
    _stderr: "_StreamBuffer" | None = field(default=None, repr=False)
    _tasks: list[asyncio.Task[Any]] = field(default_factory=list, repr=False)
    _finished_at: float | None = field(default=None, repr=False)
    _killed: bool = field(default=False, repr=False)


class ShellJobManager:
    """Manage background shell jobs inside one runtime process."""

    def __init__(self, *, max_buffer_bytes: int = DEFAULT_MAX_BUFFER_BYTES) -> None:
        if (
            isinstance(max_buffer_bytes, bool)
            or not isinstance(max_buffer_bytes, int)
            or max_buffer_bytes < 1
        ):
            raise ValueError("max_buffer_bytes must be greater than 0.")
        self.max_buffer_bytes = int(max_buffer_bytes)
        self._jobs: dict[str, ShellJob] = {}

    async def start(
        self,
        command: str,
        cwd: str | Path,
        description: str = "",
        env: Mapping[str, Any] | None = None,
    ) -> ShellJob:
        """Start a background shell command and return its registered job."""

        if not isinstance(command, str) or not command:
            raise ValueError("command must be a non-empty string.")
        resolved_cwd = str(Path(cwd).expanduser().resolve())
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=resolved_cwd,
            env=_subprocess_env(env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        job = ShellJob(
            job_id=new_id("job"),
            command=command,
            cwd=resolved_cwd,
            description=str(description or ""),
            started_at=utc_now_iso(),
            _process=process,
            _stdout=_StreamBuffer(self.max_buffer_bytes),
            _stderr=_StreamBuffer(self.max_buffer_bytes),
        )
        job._tasks = [
            asyncio.create_task(_pump_stream(process.stdout, job._stdout)),
            asyncio.create_task(_pump_stream(process.stderr, job._stderr)),
            asyncio.create_task(_watch_process(job)),
        ]
        self._jobs[job.job_id] = job
        return job

    def status(self, job_id: str) -> ShellJob:
        """Return a job or raise KeyError if the id is unknown."""

        return self._require(job_id)

    async def read(
        self,
        job_id: str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Read retained stdout/stderr text from a job."""

        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer.")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise ValueError("limit must be a positive integer.")

        job = self._require(job_id)
        stdout = await _snapshot(job._stdout)
        stderr = await _snapshot(job._stderr)
        stdout_slice = _slice_snapshot(stdout, offset=offset, limit=limit)
        stderr_slice = _slice_snapshot(stderr, offset=offset, limit=limit)
        next_offset = max(stdout_slice["next_offset"], stderr_slice["next_offset"])
        has_more = stdout_slice["has_more"] or stderr_slice["has_more"]
        return {
            **_job_payload(job),
            "offset": offset,
            "limit": limit,
            "stdout": stdout_slice["text"],
            "stderr": stderr_slice["text"],
            "has_more": has_more,
            "next_offset": next_offset,
            "truncated": stdout["truncated"] or stderr["truncated"],
            "stdout_truncated": stdout["truncated"],
            "stderr_truncated": stderr["truncated"],
            "stdout_start_offset": stdout["start_offset"],
            "stderr_start_offset": stderr["start_offset"],
            "stdout_chars": stdout["chars"],
            "stderr_chars": stderr["chars"],
        }

    async def kill(self, job_id: str) -> ShellJob:
        """Terminate a running job and return its final or terminating state."""

        job = self._require(job_id)
        process = job._process
        if process is None or job.status != "running":
            return job

        job._killed = True
        job.status = "killed"
        _terminate_process_group(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=_KILL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            _kill_process_group(process)
            await process.wait()
        job.returncode = process.returncode
        job._finished_at = time.monotonic()
        await _drain_tasks(job)
        job.status = "killed"
        return job

    def cleanup_finished(self, max_age_seconds: float | None = None) -> int:
        """Remove finished jobs, optionally only after a minimum finished age."""

        now = time.monotonic()
        removed = 0
        for job_id, job in list(self._jobs.items()):
            if job.status == "running":
                continue
            if max_age_seconds is not None:
                if job._finished_at is None:
                    continue
                if now - job._finished_at < max_age_seconds:
                    continue
            del self._jobs[job_id]
            removed += 1
        return removed

    def _require(self, job_id: str) -> ShellJob:
        try:
            return self._jobs[str(job_id)]
        except KeyError as exc:
            raise KeyError(f"Unknown background shell job: {job_id}") from exc


class _StreamBuffer:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.text = ""
        self.start_offset = 0
        self.truncated = False
        self._lock = asyncio.Lock()

    async def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        text = chunk.decode("utf-8", errors="replace")
        async with self._lock:
            self.text += text
            self._trim_locked()

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "text": self.text,
                "start_offset": self.start_offset,
                "truncated": self.truncated,
                "chars": self.start_offset + len(self.text),
            }

    def _trim_locked(self) -> None:
        encoded = self.text.encode("utf-8", errors="replace")
        if len(encoded) <= self.max_bytes:
            return
        old_chars = len(self.text)
        self.text = encoded[-self.max_bytes :].decode("utf-8", errors="replace")
        self.start_offset += max(0, old_chars - len(self.text))
        self.truncated = True


async def _pump_stream(
    stream: asyncio.StreamReader | None,
    buffer: _StreamBuffer | None,
) -> None:
    if stream is None or buffer is None:
        return
    while True:
        chunk = await stream.read(_READ_CHUNK_BYTES)
        if not chunk:
            return
        await buffer.append(chunk)


async def _watch_process(job: ShellJob) -> None:
    process = job._process
    if process is None:
        return
    await process.wait()
    job.returncode = process.returncode
    await _drain_tasks(job)
    job.status = "killed" if job._killed else "exited"
    job._finished_at = time.monotonic()


async def _drain_tasks(job: ShellJob) -> None:
    current = asyncio.current_task()
    pending = [
        task for task in job._tasks if task is not current and not task.done()
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _snapshot(buffer: _StreamBuffer | None) -> dict[str, Any]:
    if buffer is None:
        return {
            "text": "",
            "start_offset": 0,
            "truncated": False,
            "chars": 0,
        }
    return await buffer.snapshot()


def _slice_snapshot(
    snapshot: dict[str, Any],
    *,
    offset: int,
    limit: int | None,
) -> dict[str, Any]:
    text = str(snapshot["text"])
    start_offset = int(snapshot["start_offset"])
    total_chars = int(snapshot["chars"])
    start = max(offset - start_offset, 0)
    if start > len(text):
        start = len(text)
    if limit is None:
        end = len(text)
    else:
        end = min(start + limit, len(text))
    absolute_end = start_offset + end
    return {
        "text": text[start:end],
        "has_more": absolute_end < total_chars,
        "next_offset": absolute_end,
    }


def _job_payload(job: ShellJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "command": job.command,
        "cwd": job.cwd,
        "description": job.description,
        "started_at": job.started_at,
        "status": job.status,
        "returncode": job.returncode,
        "exit_code": job.returncode,
        "timed_out": False,
        "killed": job.status == "killed",
    }


def _subprocess_env(env: Mapping[str, Any] | None) -> dict[str, str] | None:
    if env is None:
        return None
    resolved = dict(os.environ)
    resolved.update({str(key): str(value) for key, value in env.items()})
    return resolved


def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, signal.SIGTERM)
            return
        except ProcessLookupError:
            return
    process.terminate()


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
    process.kill()


def _display_cwd(root: Path, cwd: str) -> str:
    try:
        return workspace_relative_path(root, Path(cwd))
    except ValueError:
        return cwd


__all__ = [
    "DEFAULT_MAX_BUFFER_BYTES",
    "ShellJob",
    "ShellJobManager",
]
