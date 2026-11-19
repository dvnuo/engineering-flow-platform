"""The shell tool stays inside the assistant's memory.

A command and the runtime share one container memory limit. These tests pin the
two ways the tool keeps a command from taking the assistant down with it: what
it holds of a command's output is bounded (a chatty command no longer grows the
runtime with every line it prints), and a command that is filling the container
is stopped on its own, with a result that says why, before the kernel kills
everything in the container.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.models import ToolCall
from efp_runtime.permissions import PermissionDecision, PermissionMetadata
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.builtin import memory_guard
from efp_runtime.tools.builtin import shell as shell_module
from efp_runtime.tools.builtin.memory_guard import MemoryReading
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.runtime import ToolRuntime


GIB = 1024 * 1024 * 1024
MIB = 1024 * 1024


class AllowEvaluator:
    async def evaluate(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        context: ToolContext | None = None,
    ) -> PermissionDecision:
        return PermissionDecision.allow()


def _runtime(tmp_path: Path) -> ToolRuntime:
    return ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )


def _python_command(script: str) -> str:
    # The tool hands the command to the platform's shell, and the two shells
    # quote differently.
    if os.name == "nt":
        return subprocess.list2cmdline([sys.executable, "-c", script])
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


async def _run_bash(tmp_path: Path, script: str, **args: Any):
    return await _runtime(tmp_path).execute(
        ToolCall(
            id="call-shell-bounds",
            tool_id="bash",
            args={
                "command": _python_command(script),
                "description": "Bounded shell command",
                **args,
            },
        )
    )


def _lines(count: int) -> bytes:
    return b"".join(b"line-%06d\n" % index for index in range(count))


# --- what the tool holds of one stream -------------------------------------


def test_capture_under_the_limit_is_exactly_what_was_written():
    data = _lines(500)
    capture = shell_module._StreamCapture(len(data) + 1)

    for start in range(0, len(data), 777):
        capture.feed(data[start : start + 777])

    assert capture.text("stdout") == (data.decode(), 0)
    assert capture.total_bytes == len(data)


def test_capture_at_the_limit_drops_nothing():
    data = _lines(400)
    capture = shell_module._StreamCapture(len(data))

    capture.feed(data)

    assert capture.text("stdout") == (data.decode(), 0)


def test_capture_over_the_limit_keeps_both_ends_and_counts_the_middle():
    data = _lines(20_000)
    capture = shell_module._StreamCapture(8192)

    for start in range(0, len(data), 1000):
        capture.feed(data[start : start + 1000])
    text, omitted = capture.text("stdout")

    kept = text.splitlines()
    marker_index = next(i for i, line in enumerate(kept) if "left out here" in line)
    head, marker, tail = kept[:marker_index], kept[marker_index], kept[marker_index + 1 :]
    assert head[0] == "line-000000"
    assert tail[-1] == "line-019999"
    # Whole lines on both sides of the cut: nothing is split mid-line.
    assert all(len(line) == len("line-000000") for line in head + tail)
    assert f"wrote {len(data)} bytes to stdout" in marker
    assert f"[{omitted} bytes left out here" in marker
    assert omitted == len(data) - sum(len(line) + 1 for line in head + tail)
    assert 0 < len(head) + len(tail) <= 8192 // len("line-000000\n")


def test_capture_result_does_not_depend_on_how_the_output_arrives():
    data = _lines(5_000)
    by_small_reads = shell_module._StreamCapture(4096)
    by_one_read = shell_module._StreamCapture(4096)

    for start in range(0, len(data), 13):
        by_small_reads.feed(data[start : start + 13])
    by_one_read.feed(data)

    assert by_small_reads.text("stderr") == by_one_read.text("stderr")


def test_capture_never_holds_much_more_than_its_limit():
    capture = shell_module._StreamCapture(64 * 1024)
    chunk = b"x" * (16 * 1024)

    for _ in range(4096):  # 64 MiB through a 64 KiB capture
        capture.feed(chunk)
        held = len(capture._head) + capture._tail_bytes
        assert held <= 64 * 1024 + len(chunk)

    text, omitted = capture.text("stdout")
    assert capture.total_bytes == 4096 * len(chunk)
    # One unbroken line: there is no line boundary to cut at, and the count still adds up.
    assert omitted == capture.total_bytes - 64 * 1024
    assert len(text) < 64 * 1024 + 512


def test_capture_limit_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(shell_module.CAPTURE_MAX_BYTES_ENV, raising=False)
    assert shell_module._capture_max_bytes() == shell_module.DEFAULT_CAPTURE_MAX_BYTES

    for raw, expected in [
        ("65536", 65536),
        ("12", shell_module._CAPTURE_MIN_BYTES),
        ("0", shell_module.DEFAULT_CAPTURE_MAX_BYTES),
        ("-5", shell_module.DEFAULT_CAPTURE_MAX_BYTES),
        ("lots", shell_module.DEFAULT_CAPTURE_MAX_BYTES),
        ("  ", shell_module.DEFAULT_CAPTURE_MAX_BYTES),
    ]:
        monkeypatch.setenv(shell_module.CAPTURE_MAX_BYTES_ENV, raw)
        assert shell_module._capture_max_bytes() == expected


def test_bounded_text_leaves_short_text_alone_and_keeps_both_ends_of_long_text():
    assert shell_module._bounded_text("short", 100) == ("short", False)

    text = "".join(f"{index:05d}\n" for index in range(1000))
    bounded, cut = shell_module._bounded_text(text, 600)

    assert cut is True
    assert bounded.startswith(text[:150])
    assert bounded.endswith(text[-450:])
    assert f"[{len(text) - 600} characters left out of this record" in bounded
    assert len(bounded) < 600 + 120


# --- the tool, end to end ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_small_command_is_recorded_exactly_as_before(tmp_path: Path):
    result = await _run_bash(
        tmp_path,
        "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)",
    )

    assert result.status == "success"
    assert result.output["stdout"].splitlines() == ["out"]
    assert result.output["stderr"].splitlines() == ["err"]
    assert result.output["exit_code"] == 7
    assert set(result.output) == {
        "stdout",
        "stderr",
        "exit_code",
        "timed_out",
        "cancelled",
        "cwd",
        "duration_ms",
    }
    assert result.metadata["capture_omitted_bytes"] == 0
    assert result.metadata["capture_max_bytes"] == shell_module.DEFAULT_CAPTURE_MAX_BYTES
    assert result.metadata["stdout_bytes"] >= len("out\n")
    assert "memory_exceeded" not in result.metadata
    assert "<stdout>\nout" in result.content
    assert "<shell_metadata>" not in result.content


@pytest.mark.asyncio
async def test_output_past_the_capture_limit_keeps_both_ends_everywhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(shell_module.CAPTURE_MAX_BYTES_ENV, "4096")

    result = await _run_bash(
        tmp_path,
        "for i in range(5000): print('line-{:06d}'.format(i))",
    )

    assert result.status == "success"
    assert result.output["exit_code"] == 0
    assert result.output["stdout_truncated"] is True
    assert "stderr_truncated" not in result.output
    assert result.metadata["capture_max_bytes"] == 4096
    assert result.metadata["stdout_bytes"] >= 5000 * len("line-000000\n")
    assert 0 < result.metadata["capture_omitted_bytes"] < result.metadata["stdout_bytes"]
    assert result.metadata["stdout_chars"] < 4096 + 512

    # The model still reads the end of the output, as it always has.
    assert result.truncated is True
    assert "line-004999" in result.content

    saved = (tmp_path / result.metadata["output_path"]).read_text(encoding="utf-8")
    assert "line-000000" in saved
    assert "line-004999" in saved
    assert "bytes left out here" in saved
    assert "Redirect the output to a file" in saved
    assert len(saved) < 4096 + 1024


@pytest.mark.asyncio
async def test_long_output_is_saved_whole_while_the_session_record_stays_small(tmp_path: Path):
    line_count = 12_000  # ~144k characters: past the record's bound, far below the capture limit

    result = await _run_bash(
        tmp_path,
        "for i in range({}): print('line-{{:06d}}'.format(i))".format(line_count),
    )

    assert result.metadata["capture_omitted_bytes"] == 0
    saved = (tmp_path / result.metadata["output_path"]).read_text(encoding="utf-8")
    assert sum(1 for line in saved.splitlines() if line.startswith("line-")) == line_count

    recorded = result.output["stdout"]
    assert result.output["stdout_truncated"] is True
    assert len(recorded) < shell_module.STATE_OUTPUT_MAX_CHARS + 200
    assert recorded.startswith("line-000000")
    assert recorded.rstrip().endswith("line-{:06d}".format(line_count - 1))
    assert "characters left out of this record" in recorded
    assert "line-{:06d}".format(line_count - 1) in result.content


# --- reading the container's memory ----------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")


def test_cgroup_v2_reading_leaves_the_page_cache_out(tmp_path: Path):
    _write(tmp_path / "memory.max", f"{2 * GIB}\n")
    _write(tmp_path / "memory.current", "2000000000\n")
    _write(
        tmp_path / "memory.stat",
        "anon 1500000000\nfile 420000000\ninactive_file 300000000\n"
        "active_file 100000000\nslab_reclaimable 50000000\nshmem 20000000\n",
    )

    reading = memory_guard.read_container_memory(tmp_path)

    assert reading == MemoryReading(limit_bytes=2 * GIB, used_bytes=1_550_000_000)
    assert 72 < reading.used_percent < 73


def test_cgroup_v2_without_a_limit_is_not_guarded(tmp_path: Path):
    _write(tmp_path / "memory.max", "max\n")
    _write(tmp_path / "memory.current", "123456\n")

    assert memory_guard.read_container_memory(tmp_path) is None


def test_cgroup_v1_reading(tmp_path: Path):
    _write(tmp_path / "memory" / "memory.limit_in_bytes", f"{GIB}\n")
    _write(tmp_path / "memory" / "memory.usage_in_bytes", "900000000\n")
    _write(
        tmp_path / "memory" / "memory.stat",
        "cache 1\ninactive_file 5\nactive_file 5\n"
        "total_inactive_file 200000000\ntotal_active_file 100000000\n",
    )

    reading = memory_guard.read_container_memory(tmp_path)

    assert reading == MemoryReading(limit_bytes=GIB, used_bytes=600_000_000)


def test_cgroup_v1_unlimited_and_missing_files_are_not_guarded(tmp_path: Path):
    assert memory_guard.read_container_memory(tmp_path / "absent") is None

    _write(tmp_path / "memory" / "memory.limit_in_bytes", "9223372036854771712\n")
    _write(tmp_path / "memory" / "memory.usage_in_bytes", "1000\n")
    assert memory_guard.read_container_memory(tmp_path) is None

    _write(tmp_path / "memory.max", "not a number\n")
    assert memory_guard.read_container_memory(tmp_path) is None


def _stat_line(pid: int, name: str, session: int, resident_pages: int) -> str:
    fields = ["S", "1", str(session), str(session)] + ["0"] * 17 + [str(resident_pages)] + ["0"] * 10
    return f"{pid} ({name}) " + " ".join(fields) + "\n"


def test_session_memory_adds_up_every_process_of_each_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(memory_guard, "_page_size", lambda: 4096)
    _write(tmp_path / "101" / "stat", _stat_line(101, "sh", 101, 10))
    # A name with spaces and parentheses must not shift the fields.
    _write(tmp_path / "102" / "stat", _stat_line(102, "my (odd) scan) x", 101, 1000))
    _write(tmp_path / "200" / "stat", _stat_line(200, "python", 200, 300))
    _write(tmp_path / "300" / "stat", _stat_line(300, "runtime", 1, 9999))
    _write(tmp_path / "301" / "stat", "301 (gone)\n")
    _write(tmp_path / "self" / "stat", _stat_line(1, "self", 101, 5000))
    (tmp_path / "400").mkdir()

    totals = memory_guard.session_memory_bytes([101, 200, 555], tmp_path)

    assert totals == {101: 1010 * 4096, 200: 300 * 4096, 555: 0}
    assert memory_guard.session_memory_bytes([], tmp_path) == {}
    assert memory_guard.session_memory_bytes([7], tmp_path / "absent") == {7: 0}


def test_guard_percent_reads_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(memory_guard.GUARD_PERCENT_ENV, raising=False)
    assert memory_guard.guard_percent() == 90

    for raw, expected in [("75", 75), ("0", 0), ("-1", 0), ("150", 100), ("off", 90), (" ", 90)]:
        monkeypatch.setenv(memory_guard.GUARD_PERCENT_ENV, raw)
        assert memory_guard.guard_percent() == expected


# --- stopping the command that is filling the container ---------------------


def _pressure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    used: int,
    sizes: dict[int, int] | None,
    limit: int = 2 * GIB,
    once: Path | None = None,
):
    """Pretend the container holds ``used`` of ``limit``; ``sizes`` is each running command's share.

    With ``once`` the container looks half empty until that file exists, so a
    test command can print first and only then appear to fill the container.
    """

    def reading() -> MemoryReading:
        if once is not None and not once.exists():
            return MemoryReading(limit_bytes=limit, used_bytes=limit // 2)
        return MemoryReading(limit_bytes=limit, used_bytes=used)

    monkeypatch.setattr(shell_module, "read_container_memory", reading)
    monkeypatch.setattr(
        shell_module,
        "session_memory_bytes",
        lambda ids: {pid: (GIB if sizes is None else sizes.get(pid, 0)) for pid in ids},
    )


def test_only_the_largest_command_is_stopped_and_only_under_pressure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shell_module, "_RUNNING_COMMANDS", {11, 22})
    limit = 2 * GIB

    _pressure(monkeypatch, used=int(limit * 0.95), sizes={11: 900 * MIB, 22: 600 * MIB})
    stop = shell_module._memory_stop_for(11, 90)
    assert stop == shell_module._MemoryStop(
        limit_bytes=limit,
        used_bytes=int(limit * 0.95),
        command_bytes=900 * MIB,
    )
    # The smaller command is left running while the larger one is stopped.
    assert shell_module._memory_stop_for(22, 90) is None

    # Below the threshold nothing is stopped, however large the command.
    _pressure(monkeypatch, used=int(limit * 0.89), sizes={11: 1800 * MIB, 22: 0})
    assert shell_module._memory_stop_for(11, 90) is None

    # Pressure that does not come from the command is not relieved by stopping it.
    _pressure(monkeypatch, used=int(limit * 0.97), sizes={11: 100 * MIB, 22: 50 * MIB})
    assert shell_module._memory_stop_for(11, 90) is None

    # No limit to read: nothing to guard.
    monkeypatch.setattr(shell_module, "read_container_memory", lambda: None)
    assert shell_module._memory_stop_for(11, 90) is None


def _announce_then_sleep(marker: Path | None, seconds: int) -> str:
    """A script that prints, then (optionally) creates ``marker``, then sleeps."""

    touch = "" if marker is None else "open({!r}, 'w').close(); ".format(marker.as_posix())
    return "import time; print('started', flush=True); {}time.sleep({})".format(touch, seconds)


async def _sleeping_process(seconds: int = 30, marker: Path | None = None) -> asyncio.subprocess.Process:
    # The process directly, not through a shell: on Windows killing the shell
    # leaves the command it started holding the pipes open.
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _announce_then_sleep(marker, seconds),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


@pytest.mark.asyncio
async def test_a_command_filling_the_container_is_stopped_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(shell_module, "_MEMORY_POLL_SECONDS", 0.05)
    marker = tmp_path / "filled"
    _pressure(monkeypatch, used=int(2 * GIB * 0.95), sizes=None, once=marker)
    process = await _sleeping_process(marker=marker)

    outcome = await asyncio.wait_for(
        shell_module._communicate_with_timeout_and_cancel(
            process,
            timeout=30,
            context=ToolContext(cancel_requested=lambda: False),
        ),
        timeout=10,
    )

    assert outcome.memory_stop == shell_module._MemoryStop(
        limit_bytes=2 * GIB,
        used_bytes=int(2 * GIB * 0.95),
        command_bytes=GIB,
    )
    assert outcome.timed_out is False
    assert outcome.cancelled is False
    assert outcome.exit_code is None
    assert process.returncode is not None
    assert outcome.stdout.text("stdout")[0].startswith("started")
    assert shell_module._RUNNING_COMMANDS == set()


@pytest.mark.asyncio
async def test_without_a_memory_limit_the_command_just_runs(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shell_module, "_MEMORY_POLL_SECONDS", 0.01)
    monkeypatch.setattr(shell_module, "read_container_memory", lambda: None)
    process = await _sleeping_process(seconds=0)

    outcome = await asyncio.wait_for(
        shell_module._communicate_with_timeout_and_cancel(
            process,
            timeout=30,
            context=ToolContext(cancel_requested=lambda: False),
        ),
        timeout=10,
    )

    assert outcome.memory_stop is None
    assert outcome.timed_out is False
    assert outcome.exit_code == 0
    assert shell_module._RUNNING_COMMANDS == set()


@pytest.mark.asyncio
async def test_the_guard_switched_off_never_stops_a_command(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shell_module, "_MEMORY_POLL_SECONDS", 0.01)
    monkeypatch.setenv(memory_guard.GUARD_PERCENT_ENV, "0")
    _pressure(monkeypatch, used=int(2 * GIB * 0.99), sizes=None)
    process = await _sleeping_process(seconds=1)

    outcome = await asyncio.wait_for(
        shell_module._communicate_with_timeout_and_cancel(
            process,
            timeout=30,
            context=ToolContext(cancel_requested=lambda: False),
        ),
        timeout=10,
    )

    assert outcome.memory_stop is None
    assert outcome.exit_code == 0


@pytest.mark.asyncio
async def test_a_failing_memory_reading_never_fails_the_command(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shell_module, "_MEMORY_POLL_SECONDS", 0.01)
    calls = 0

    def flaky_reading():
        nonlocal calls
        calls += 1
        if calls == 1:
            return MemoryReading(limit_bytes=2 * GIB, used_bytes=GIB)
        raise OSError("cgroup files went away")

    monkeypatch.setattr(shell_module, "read_container_memory", flaky_reading)
    process = await _sleeping_process(seconds=1)

    outcome = await asyncio.wait_for(
        shell_module._communicate_with_timeout_and_cancel(
            process,
            timeout=30,
            context=ToolContext(cancel_requested=lambda: False),
        ),
        timeout=10,
    )

    assert calls >= 2
    assert outcome.memory_stop is None
    assert outcome.exit_code == 0
    assert shell_module._RUNNING_COMMANDS == set()


@pytest.mark.asyncio
async def test_the_result_tells_the_model_why_the_command_was_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(shell_module, "_MEMORY_POLL_SECONDS", 0.05)
    marker = tmp_path / "filled"
    _pressure(monkeypatch, used=1946 * MIB, sizes=None, once=marker)

    result = await asyncio.wait_for(
        _run_bash(tmp_path, _announce_then_sleep(marker, 2), timeout=20_000),
        timeout=15,
    )

    assert result.status == "success"
    assert result.output["memory_exceeded"] is True
    assert result.output["timed_out"] is False
    assert result.output["cancelled"] is False
    assert result.output["exit_code"] is None
    assert result.metadata["memory_exceeded"] is True
    assert result.metadata["timed_out"] is False
    assert result.metadata["memory_limit_bytes"] == 2 * GIB
    assert result.metadata["memory_used_bytes"] == 1946 * MIB
    assert result.metadata["command_memory_bytes"] == GIB
    assert "started" in result.content
    assert "<shell_metadata>" in result.content
    assert "before the assistant ran out of memory" in result.content
    assert "1946 MiB of its 2048 MiB memory limit" in result.content
    assert "1024 MiB of them by this command" in result.content
    assert "Only this command was stopped" in result.content
    assert "exceeding timeout" not in result.content


def test_a_command_the_system_killed_is_explained():
    content = shell_module._format_shell_content(
        stdout="partial\n",
        stderr="",
        timed_out=False,
        cancelled=False,
        timeout_ms=30_000,
        killed_by_system=True,
    )

    assert content.startswith("<stdout>\npartial\n</stdout>")
    assert "SIGKILL, exit code 137" in content
    assert "needed more memory than the assistant has" in content

    plain = shell_module._format_shell_content(
        stdout="ok\n",
        stderr="",
        timed_out=False,
        cancelled=False,
        timeout_ms=30_000,
    )
    assert plain == "<stdout>\nok\n</stdout>"


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL exit statuses are a POSIX shell behaviour")
@pytest.mark.asyncio
async def test_a_sigkilled_command_says_so_in_its_result(tmp_path: Path):
    result = await _runtime(tmp_path).execute(
        ToolCall(
            id="call-shell-sigkill",
            tool_id="bash",
            args={
                "command": "echo before; kill -9 $$",
                "description": "Command killed by the system",
            },
        )
    )

    assert result.status == "success"
    assert result.output["exit_code"] in {-9, 137}
    assert result.output["timed_out"] is False
    assert "before" in result.content
    assert "SIGKILL, exit code 137" in result.content
    assert "memory_exceeded" not in result.output
