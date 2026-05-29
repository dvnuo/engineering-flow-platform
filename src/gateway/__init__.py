"""Gateway package for Engineering Flow Platform."""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "gateway":
        from .server import gateway

        return gateway
    if name == "Gateway":
        from .server import Gateway

        return Gateway
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["gateway", "Gateway"]
