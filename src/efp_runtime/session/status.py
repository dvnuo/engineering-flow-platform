"""Runtime v2 session status values."""

from enum import Enum


class RuntimeStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    FINISHED = "finished"
    ERROR = "error"
