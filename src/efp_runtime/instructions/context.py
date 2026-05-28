"""Transient instruction context messages for EFP Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..session.models import Message, MessagePart, MessageRole


DEFAULT_INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "CONTEXT.md")
_TRUNCATION_NOTICE = "[Instruction content truncated to {kept} of {original} chars.]"


class InstructionContextBuilder:
    """Build system messages from workspace and explicit instruction sources."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None,
        instruction_paths: Iterable[str | Path] = (),
        instruction_texts: Iterable[str] = (),
        include_default_files: bool = True,
        default_file_names: Iterable[str] = DEFAULT_INSTRUCTION_FILES,
        max_instruction_chars: int = 20000,
    ) -> None:
        if max_instruction_chars < 0:
            raise ValueError("max_instruction_chars must be greater than or equal to 0")
        self.workspace_root = _coerce_optional_path(workspace_root)
        self.instruction_paths = list(instruction_paths)
        self.instruction_texts = list(instruction_texts)
        self.include_default_files = bool(include_default_files)
        self.default_file_names = [str(name) for name in default_file_names]
        self.max_instruction_chars = max_instruction_chars

    def build_messages(self) -> list[Message]:
        messages: list[Message] = []
        seen_paths: set[Path] = set()

        for path in self._candidate_paths():
            resolved_path = _resolved_file_path(path)
            if resolved_path is None or resolved_path in seen_paths:
                continue
            seen_paths.add(resolved_path)
            text = _read_text_file(resolved_path)
            if text is None:
                continue
            messages.append(self._file_message(resolved_path, text))

        for text in self.instruction_texts:
            content = str(text)
            if not content.strip():
                continue
            messages.append(self._inline_message(content))

        return messages

    def _candidate_paths(self) -> list[Path]:
        paths: list[Path] = []
        root = self.workspace_root
        if self.include_default_files and root is not None and root.exists():
            for name in self.default_file_names:
                if name:
                    paths.append(root / name)

        for raw_path in self.instruction_paths:
            path = _resolve_configured_path(raw_path, workspace_root=root)
            if path is not None:
                paths.append(path)

        return paths

    def _file_message(self, path: Path, content: str) -> Message:
        rendered_content, truncated = self._truncate_content(content)
        metadata = {
            "kind": "instruction_context",
            "source": "file",
            "path": str(path),
            "truncated": truncated,
            "original_chars": len(content),
        }
        return _system_text_message(
            f"Instructions from: {path}\n{rendered_content}",
            metadata=metadata,
        )

    def _inline_message(self, content: str) -> Message:
        rendered_content, truncated = self._truncate_content(content)
        metadata = {
            "kind": "instruction_context",
            "source": "inline",
            "truncated": truncated,
            "original_chars": len(content),
        }
        return _system_text_message(rendered_content, metadata=metadata)

    def _truncate_content(self, content: str) -> tuple[str, bool]:
        original_chars = len(content)
        if original_chars <= self.max_instruction_chars:
            return content, False
        kept = self.max_instruction_chars
        notice = _TRUNCATION_NOTICE.format(kept=kept, original=original_chars)
        if kept == 0:
            return notice, True
        return f"{content[:kept]}\n\n{notice}", True


def _system_text_message(text: str, *, metadata: dict[str, object]) -> Message:
    return Message(
        role=MessageRole.SYSTEM,
        parts=[MessagePart.text_part(text, metadata=metadata)],
        metadata=metadata,
    )


def _coerce_optional_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    if isinstance(path, str) and not path.strip():
        return None
    return Path(path).expanduser()


def _resolve_configured_path(
    path: str | Path,
    *,
    workspace_root: Path | None,
) -> Path | None:
    if isinstance(path, str) and not path.strip():
        return None
    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return expanded
    if workspace_root is None:
        return None
    return workspace_root / expanded


def _resolved_file_path(path: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return None
    if not resolved.is_file():
        return None
    return resolved


def _read_text_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
