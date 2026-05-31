"""Tool selection controls for EFP runtime."""

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


@dataclass(frozen=True)
class ModelAwareToolSelection:
    """Outcome of model-hinted mutating file tool selection."""

    enabled: bool
    ran: bool
    model_hint: str | None
    mode: str
    forced_disabled: tuple[str, ...] = ()


MODEL_HINT_KEYS = (
    "model",
    "model_id",
    "requested_model",
    "provider_model",
    "default_model",
)
PATCH_FILE_TOOL_ID = "apply_patch"
DIRECT_FILE_TOOL_IDS = frozenset({"edit", "write"})


def resolve_model_aware_tool_selection(
    all_tool_ids: Iterable[str],
    metadata: Mapping[str, object] | None = None,
    *,
    enabled: bool = True,
) -> ModelAwareToolSelection:
    """Return model-hinted forced-disabled mutating file tool ids."""

    all_ids = {str(tool_id) for tool_id in all_tool_ids}
    model_hint = _model_hint(metadata or {})
    if not enabled:
        return ModelAwareToolSelection(
            enabled=False,
            ran=False,
            model_hint=model_hint,
            mode="none",
        )
    if model_hint is None:
        return ModelAwareToolSelection(
            enabled=True,
            ran=False,
            model_hint=None,
            mode="none",
        )

    if _prefers_patch_file_tool(model_hint):
        forced_disabled = sorted(DIRECT_FILE_TOOL_IDS.intersection(all_ids))
        return ModelAwareToolSelection(
            enabled=True,
            ran=True,
            model_hint=model_hint,
            mode="patch",
            forced_disabled=tuple(forced_disabled),
        )

    forced_disabled = (PATCH_FILE_TOOL_ID,) if PATCH_FILE_TOOL_ID in all_ids else ()
    return ModelAwareToolSelection(
        enabled=True,
        ran=True,
        model_hint=model_hint,
        mode="direct",
        forced_disabled=forced_disabled,
    )


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


def _model_hint(metadata: Mapping[str, object]) -> str | None:
    for key in MODEL_HINT_KEYS:
        value = metadata.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _prefers_patch_file_tool(model_hint: str) -> bool:
    normalized = model_hint.lower()
    return (
        "gpt-" in normalized
        and "gpt-4" not in normalized
        and "oss" not in normalized
    )


__all__ = [
    "DIRECT_FILE_TOOL_IDS",
    "MODEL_HINT_KEYS",
    "ModelAwareToolSelection",
    "PATCH_FILE_TOOL_ID",
    "ToolSelection",
    "resolve_model_aware_tool_selection",
    "resolve_tool_selection",
]
