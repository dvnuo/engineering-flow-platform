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
    """Resident memory of every process in each session (a command and what it started).

    The shell tool starts each command in a session of its own, so the session
    id is the command's process id. Shared pages are counted once per process,
    which overstates the total; the figures are used to tell which command is
    the large one, not to account for memory.
    """

    totals = {int(session_id): 0 for session_id in session_ids}
    if not totals:
        return totals
    base = PROC_ROOT if proc_root is None else Path(proc_root)
    try:
        entries = list(os.scandir(base))
    except OSError:
        return totals
    page_size = _page_size()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        fields = _read_stat_fields(Path(entry.path) / "stat")
        if fields is None:
            continue
        session, resident_pages = fields
        if session in totals:
            totals[session] += resident_pages * page_size
    return totals


def _read_cgroup_v2(base: Path) -> MemoryReading | None:
    limit = _read_int(base / "memory.max")
    current = _read_int(base / "memory.current")
    if limit is None or current is None or limit <= 0 or limit >= _UNLIMITED_BYTES:
        return None
    stat = _read_stat_file(base / "memory.stat")
    reclaimable = (
        stat.get("inactive_file", 0)
        + stat.get("active_file", 0)
        + stat.get("slab_reclaimable", 0)
    )
    return MemoryReading(limit_bytes=limit, used_bytes=max(current - reclaimable, 0))


def _read_cgroup_v1(base: Path) -> MemoryReading | None:
    limit = _read_int(base / "memory.limit_in_bytes")
    current = _read_int(base / "memory.usage_in_bytes")
    if limit is None or current is None or limit <= 0 or limit >= _UNLIMITED_BYTES:
        return None
    stat = _read_stat_file(base / "memory.stat")
    reclaimable = stat.get(
        "total_inactive_file", stat.get("inactive_file", 0)
    ) + stat.get("total_active_file", stat.get("active_file", 0))
    return MemoryReading(limit_bytes=limit, used_bytes=max(current - reclaimable, 0))


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


def _read_stat_fields(path: Path) -> tuple[int, int] | None:
    """Session id and resident pages from a ``/proc/<pid>/stat`` line."""

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
        return int(fields[3]), int(fields[21])
    except ValueError:
        return None


def _page_size() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, ValueError, OSError):
        return 4096
