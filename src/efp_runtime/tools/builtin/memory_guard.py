"""Container memory readings for the shell tool's memory guard.

An assistant's runtime and every command it starts share one container memory
limit. When a command pushes the container past that limit the kernel kills
processes in it, and on current Kubernetes nodes (cgroup v2) it kills all of
them: the assistant restarts and the turn in progress is lost. The shell tool
uses the readings here to stop the command that is filling the container
shortly before that point, so the model gets an error it can act on instead.

Everything here is read-only and best-effort: outside a memory-limited Linux
container every function reports "nothing to go on" and the shell tool behaves
exactly as it did before.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable


CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_ROOT = Path("/proc")

DEFAULT_GUARD_PERCENT = 90
# A command is only stopped when its own processes hold at least this share of
# the limit; below that the pressure comes from somewhere else and stopping the
# command would not relieve it.
COMMAND_SHARE_FLOOR_PERCENT = 10
GUARD_PERCENT_ENV = "EFP_SHELL_MEMORY_GUARD_PERCENT"

# cgroup v1 reports "no limit" as a number near 2**63; anything this large is
# not a limit a container can reach.
_UNLIMITED_BYTES = 1 << 60
# Process states in /proc/<pid>/stat of a process that has ended and only waits
# to be collected: there is nothing left of it to stop.
_DEAD_STATES = frozenset({"Z", "X", "x"})


@dataclass(frozen=True)
class MemoryReading:
    """How full the container is, counting only memory the kernel cannot free.

    ``used_bytes`` leaves the page cache out: reading a large repository fills
    the cache up to the limit, and the kernel drops it on demand, so counting
    it would stop commands that were never in danger.
    """

    limit_bytes: int
    used_bytes: int

    @property
    def used_percent(self) -> float:
        if self.limit_bytes <= 0:
            return 0.0
        return self.used_bytes * 100.0 / self.limit_bytes


def guard_percent() -> int:
    """The share of the limit at which a command is stopped; 0 turns the guard off."""

    raw = os.environ.get(GUARD_PERCENT_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_GUARD_PERCENT
    try:
        value = int(raw.strip())
    except ValueError:
        return DEFAULT_GUARD_PERCENT
    if value <= 0:
        return 0
    return min(value, 100)


def read_container_memory(root: Path | None = None) -> MemoryReading | None:
    """Read the container's memory limit and unreclaimable use, or None when there is no limit."""

    base = CGROUP_ROOT if root is None else Path(root)
    reading = _read_cgroup_v2(base)
    if reading is not None:
        return reading
    return _read_cgroup_v1(base / "memory")


def session_memory_bytes(
    session_ids: Iterable[int],
    proc_root: Path | None = None,
) -> dict[int, int]:
    """Memory of its own that every process in each session holds (a command and what it started).

    The shell tool starts each command in a session of its own, so the session
    id is the command's process id. Only anonymous memory is counted. Pages
    backed by a file (a mapped pack file, shared libraries) are left out, as
    they are from the container reading: the kernel can drop them, and counting
    them would blame a ``git`` command with large mapped files for pressure it
    did not cause. Shared memory is left out with them, because the kernel
    reports the two as one figure; a command that fills the container through
    ``/dev/shm`` is therefore not recognised as the one doing it.
    """

    totals = {int(session_id): 0 for session_id in session_ids}
    if not totals:
        return totals
    base = PROC_ROOT if proc_root is None else Path(proc_root)
    page_size = _page_size()
    for directory, _state, session, resident_pages in _processes(base):
        if session not in totals:
            continue
        anonymous_pages = _read_anonymous_pages(directory / "statm")
        if anonymous_pages is None:
            anonymous_pages = resident_pages
        totals[session] += anonymous_pages * page_size
    return totals


def session_process_ids(
    session_id: int,
    proc_root: Path | None = None,
    *,
    include_ended: bool = False,
) -> list[int]:
    """The processes of one session, by process id.

    A process that has ended and only waits to be collected is left out unless
    ``include_ended`` is set. One whose first thread has ended while others
    still run reads the same way, which is why the code that stops a session
    asks for all of them.
    """

    base = PROC_ROOT if proc_root is None else Path(proc_root)
    wanted = int(session_id)
    return [
        int(directory.name)
        for directory, state, session, _resident in _processes(base)
        if session == wanted and (include_ended or state not in _DEAD_STATES)
    ]


def _processes(base: Path):
    """``(directory, state, session, resident pages)`` of every process under ``base``."""

    try:
        entries = list(os.scandir(base))
    except OSError:
        return
    for entry in entries:
        if not entry.name.isdigit():
            continue
        directory = Path(entry.path)
        fields = _read_stat_fields(directory / "stat")
        if fields is not None:
            yield (directory, *fields)


def _read_cgroup_v2(base: Path) -> MemoryReading | None:
    limit = _read_int(base / "memory.max")
    current = _read_int(base / "memory.current")
    if limit is None or current is None or limit <= 0 or limit >= _UNLIMITED_BYTES:
        return None
    stat = _read_stat_file(base / "memory.stat")
    inactive_file = stat.get("inactive_file")
    active_file = stat.get("active_file")
    if inactive_file is None or active_file is None:
        # Without the page cache figures the cache would count as use, and a
        # container whose cache has filled the limit would look nearly full.
        return None
    reclaimable = inactive_file + active_file + stat.get("slab_reclaimable", 0)
    return MemoryReading(limit_bytes=limit, used_bytes=max(current - reclaimable, 0))


def _read_cgroup_v1(base: Path) -> MemoryReading | None:
    limit = _read_int(base / "memory.limit_in_bytes")
    current = _read_int(base / "memory.usage_in_bytes")
    if limit is None or current is None or limit <= 0 or limit >= _UNLIMITED_BYTES:
        return None
    stat = _read_stat_file(base / "memory.stat")
    inactive_file = stat.get("total_inactive_file", stat.get("inactive_file"))
    active_file = stat.get("total_active_file", stat.get("active_file"))
    if inactive_file is None or active_file is None:
        return None
    return MemoryReading(limit_bytes=limit, used_bytes=max(current - inactive_file - active_file, 0))


def _read_int(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return None
    if not text or text == "max":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _read_stat_file(path: Path) -> dict[str, int]:
    try:
        text = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return {}
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, raw = line.partition(" ")
        try:
            values[key] = int(raw.strip())
        except ValueError:
            continue
    return values


def _read_stat_fields(path: Path) -> tuple[str, int, int] | None:
    """State, session id and resident pages from a ``/proc/<pid>/stat`` line."""

    try:
        text = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    # The command name sits in parentheses and may itself contain spaces and
    # parentheses; the numeric fields start after the last closing one.
    _, separator, rest = text.rpartition(")")
    if not separator:
        return None
    fields = rest.split()
    # After the name: state, ppid, pgrp, session, ... and rss as the 22nd.
    if len(fields) < 22:
        return None
    try:
        return fields[0], int(fields[3]), int(fields[21])
    except ValueError:
        return None


def _read_anonymous_pages(path: Path) -> int | None:
    """Resident pages that are not backed by a file, from a ``/proc/<pid>/statm`` line."""

    try:
        fields = path.read_text(encoding="ascii", errors="replace").split()
    except OSError:
        return None
    # size, resident, shared (resident pages backed by a file or shared memory), ...
    if len(fields) < 3:
        return None
    try:
        return max(int(fields[1]) - int(fields[2]), 0)
    except ValueError:
        return None


def _page_size() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, ValueError, OSError):
        return 4096
