"""Provider-only system prompt stack for EFP Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .session.models import Message, MessagePart, MessageRole


DEFAULT_SYSTEM_PROMPT = """You are EFP Runtime v2, an interactive software engineering agent working in a shared workspace.

Core operating rules:
- Use available tools to inspect files, run commands, and modify code; do not invent command output, file contents, tool results, or runtime state.
- Read or search relevant code before editing. Follow existing style, patterns, and local conventions.
- Prefer specialized tools for file reads, searches, edits, and structured operations when they are available; use shell for tasks better handled there.
- Keep responses concise and direct, like a CLI coding agent, unless the user asks for detail.
- Preserve user changes. Do not revert unrelated work or overwrite changes you did not make.
- Run focused tests or validation when practical; report what you ran, and say when validation could not be run.
- Do not commit changes unless the user explicitly asks.
- If a question is truly blocking after reading relevant context and the question tool is enabled, use it instead of guessing.
- When citing code, prefer path:line references.
"""

_TRUNCATION_NOTICE = "[System prompt content truncated to {kept} of {original} chars.]"


@dataclass(frozen=True)
class SystemPromptSource:
    """One source included in the provider-only system prompt stack."""

    path: str | None
    content: str
    truncated: bool
    original_chars: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))


class SystemPromptBuilder:
    """Build transient system prompt and runtime reminder messages."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None,
        include_default_system_prompt: bool = True,
        system_prompt_texts: Iterable[str] = (),
        system_prompt_paths: Iterable[str | Path] = (),
        max_system_prompt_chars: int = 20000,
        include_runtime_reminders: bool = True,
    ) -> None:
        if max_system_prompt_chars < 0:
            raise ValueError("max_system_prompt_chars must be greater than or equal to 0")
        self.workspace_root = _coerce_workspace_root(workspace_root)
        self.include_default_system_prompt = bool(include_default_system_prompt)
        self.system_prompt_texts = list(system_prompt_texts)
        self.system_prompt_paths = list(system_prompt_paths)
        self.max_system_prompt_chars = max_system_prompt_chars
        self.include_runtime_reminders = bool(include_runtime_reminders)

    def build_messages(self, metadata: Mapping[str, Any] | None = None) -> list[Message]:
        runtime_metadata = dict(metadata or {})
        messages: list[Message] = []

        if self.include_default_system_prompt:
            messages.append(
                self._message_from_text(
                    DEFAULT_SYSTEM_PROMPT,
                    source="default_system_prompt",
                )
            )

        for index, text in enumerate(self.system_prompt_texts):
            content = str(text)
            if not content.strip():
                continue
            messages.append(
                self._message_from_text(
                    content,
                    source="inline",
                    metadata={"index": index},
                )
            )

        seen_paths: set[Path] = set()
        for raw_path in self.system_prompt_paths:
            resolved_path = _resolve_system_prompt_path(
                raw_path,
                workspace_root=self.workspace_root,
            )
            if resolved_path is None or resolved_path in seen_paths:
                continue
            seen_paths.add(resolved_path)
            content = _read_text_file(resolved_path)
            if content is None or not content.strip():
                continue
            messages.append(
                self._message_from_text(
                    content,
                    source="file",
                    path=resolved_path,
                )
            )

        if self.include_runtime_reminders:
            reminder = self._runtime_reminder_message(runtime_metadata)
            if reminder is not None:
                messages.append(reminder)

        return messages

    def _message_from_text(
        self,
        content: str,
        *,
        source: str,
        path: Path | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Message:
        rendered_content, truncated = self._truncate_content(content)
        source_metadata = dict(metadata or {})
        source_metadata["source"] = source
        if path is not None:
            source_metadata["path"] = str(path)
        source = SystemPromptSource(
            path=str(path) if path is not None else None,
            content=rendered_content,
            truncated=truncated,
            original_chars=len(content),
            metadata=source_metadata,
        )
        return _system_text_message(source)

    def _runtime_reminder_message(
        self,
        metadata: Mapping[str, Any],
    ) -> Message | None:
        lines: list[str] = []
        max_iterations = _metadata_value(metadata, "max_iterations")
        if max_iterations is not None:
            lines.append(
                f"- This run is close-bounded by max_iterations={max_iterations}; "
                "converge on the task, avoid extra provider or tool loops, and "
                "use available task or background capabilities when appropriate."
            )
        if _metadata_bool(metadata, "enable_question_tool"):
            lines.append(
                "- Use the question tool only when truly blocked after reading relevant context; otherwise make a supported decision."
            )
        if _metadata_value(metadata, "runtime_mode") == "plan":
            lines.append(
                "- Plan mode is active: do read-only analysis, do not write files, "
                "do not run shell commands that mutate state, and finish through "
                "plan_exit when available."
            )
        if _metadata_bool(
            metadata,
            "tool_output_truncation_enabled",
            "tool_output_paths_enabled",
            "include_output_path_reminder",
        ):
            lines.append(
                "- When output is truncated, rely on saved output metadata such as output_path; "
                "use ranged read or grep instead of trusting the visible excerpt."
            )

        if not lines:
            return None

        content = "Runtime reminders:\n" + "\n".join(lines)
        source = SystemPromptSource(
            path=None,
            content=content,
            truncated=False,
            original_chars=len(content),
            metadata={
                "source": "runtime_reminders",
                "reminder_count": len(lines),
            },
        )
        return _system_text_message(source)

    def _truncate_content(self, content: str) -> tuple[str, bool]:
        original_chars = len(content)
        if original_chars <= self.max_system_prompt_chars:
            return content, False
        kept = self.max_system_prompt_chars
        notice = _TRUNCATION_NOTICE.format(kept=kept, original=original_chars)
        if kept == 0:
            return notice, True
        return f"{content[:kept]}\n\n{notice}", True


def _system_text_message(source: SystemPromptSource) -> Message:
    metadata: dict[str, Any] = {
        "context_type": "system_prompt",
        "source": source.metadata.get("source", source.path or "inline"),
        "truncated": source.truncated,
        "original_chars": source.original_chars,
    }
    if source.path is not None:
        metadata["path"] = source.path
    metadata.update(source.metadata)
    metadata["context_type"] = "system_prompt"
    metadata["truncated"] = source.truncated
    metadata["original_chars"] = source.original_chars
    return Message(
        role=MessageRole.SYSTEM,
        parts=[MessagePart.text_part(source.content, metadata=metadata)],
        metadata=metadata,
        status="complete",
    )


def _coerce_workspace_root(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    if isinstance(path, str) and not path.strip():
        return None
    try:
        return Path(path).expanduser().resolve(strict=False)
    except OSError:
        return None


def _resolve_system_prompt_path(
    path: str | Path,
    *,
    workspace_root: Path | None,
) -> Path | None:
    if workspace_root is None:
        return None
    if isinstance(path, str) and not path.strip():
        return None
    raw_path = Path(path).expanduser()
    candidate = raw_path if raw_path.is_absolute() else workspace_root / raw_path
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    if not _is_relative_to(resolved, workspace_root):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _read_text_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _metadata_value(metadata: Mapping[str, Any], key: str) -> Any:
    value = metadata.get(key)
    if value is not None:
        return value
    loop_metadata = metadata.get("loop")
    if isinstance(loop_metadata, Mapping):
        value = loop_metadata.get(key)
        if value is not None:
            return value
    runtime_config = metadata.get("runtime_config")
    if isinstance(runtime_config, Mapping):
        return runtime_config.get(key)
    return None


def _metadata_bool(metadata: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        value = _metadata_value(metadata, key)
        if value is not None:
            return bool(value)
    return False


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "SystemPromptBuilder",
    "SystemPromptSource",
]
