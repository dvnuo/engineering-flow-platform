"""Tool selection controls for Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass
class ToolSelection:
    """Configured tool selection before per-run overrides are applied."""

    enabled: set[str] | None = None
    disabled: set[str] = field(default_factory=set)
    forced_disabled: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.enabled = (
            None if self.enabled is None else {str(tool_id) for tool_id in self.enabled}
        )
        self.disabled = {str(tool_id) for tool_id in self.disabled}
        self.forced_disabled = {str(tool_id) for tool_id in self.forced_disabled}


def resolve_tool_selection(
    all_tool_ids: Iterable[str],
    *,
    enabled: Iterable[str] | None = None,
    disabled: Iterable[str] | None = None,
    forced_disabled: Iterable[str] | None = None,
    overrides: Mapping[str, bool] | None = None,
) -> list[str]:
    """Resolve enabled tool ids after configured selection and run overrides."""

    all_ids = {str(tool_id) for tool_id in all_tool_ids}
    enabled_ids = None if enabled is None else _normalize_ids(enabled)
    disabled_ids = _normalize_ids(disabled or ())
    forced_disabled_ids = _normalize_ids(forced_disabled or ())
    override_map = dict(overrides or {})
    override_ids = {str(tool_id) for tool_id in override_map}

    unknown = sorted(((enabled_ids or set()) | disabled_ids | override_ids).difference(all_ids))
    if unknown:
        if len(unknown) == 1:
            raise KeyError(f"Unknown tool: {unknown[0]}")
        raise KeyError(f"Unknown tools: {', '.join(unknown)}")

    selected = set(all_ids) if enabled_ids is None else set(enabled_ids)
    selected.difference_update(disabled_ids)

    for tool_id, allowed in override_map.items():
        normalized_id = str(tool_id)
        if allowed is True:
            selected.add(normalized_id)
        elif allowed is False:
            selected.discard(normalized_id)
        else:
            raise TypeError("tool overrides must map tool ids to bool values")

    selected.difference_update(forced_disabled_ids)
    return sorted(selected)


def _normalize_ids(tool_ids: Iterable[str]) -> set[str]:
    return {str(tool_id) for tool_id in tool_ids}


__all__ = ["ToolSelection", "resolve_tool_selection"]
