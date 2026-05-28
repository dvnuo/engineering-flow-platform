"""Transient instruction context messages for EFP Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

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


class ReadInstructionResolver:
    """Resolve instruction files near a workspace file read."""

    def __init__(
        self,
        workspace_root: str | Path,
        default_file_names: Iterable[str] = DEFAULT_INSTRUCTION_FILES,
        max_instruction_chars: int = 20000,
    ) -> None:
        if max_instruction_chars < 0:
            raise ValueError("max_instruction_chars must be greater than or equal to 0")
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.default_file_names = [str(name) for name in default_file_names if str(name)]
        self.max_instruction_chars = max_instruction_chars

    def resolve_for_path(self, path: str | Path) -> list[dict[str, Any]]:
        target = self._resolve_workspace_path(path)
        if target is None:
            return []

        entries: list[dict[str, Any]] = []
        seen_paths: set[Path] = set()
        current = target.parent if target != self.workspace_root else target

        while _is_relative_to(current, self.workspace_root):
            instruction_path = self._find_instruction_file(current)
            if instruction_path is not None:
                resolved_instruction = _resolved_file_path(instruction_path)
                if (
                    resolved_instruction is not None
                    and _is_relative_to(resolved_instruction, self.workspace_root)
                    and resolved_instruction != target
                    and resolved_instruction not in seen_paths
                ):
                    seen_paths.add(resolved_instruction)
                    content = _read_instruction_text_file(resolved_instruction)
                    if content is not None:
                        entries.append(self._entry(resolved_instruction, content))

            if current == self.workspace_root:
                break
            current = current.parent

        return entries

    def _resolve_workspace_path(self, path: str | Path) -> Path | None:
        raw_path = Path(path).expanduser()
        candidate = raw_path if raw_path.is_absolute() else self.workspace_root / raw_path
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            return None
        if not _is_relative_to(resolved, self.workspace_root):
            return None
        return resolved

    def _find_instruction_file(self, directory: Path) -> Path | None:
        for name in self.default_file_names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        return None

    def _entry(self, path: Path, content: str) -> dict[str, Any]:
        original_chars = len(content)
        truncated = original_chars > self.max_instruction_chars
        rendered_content = content[: self.max_instruction_chars] if truncated else content
        return {
            "path": _relative_posix(self.workspace_root, path),
            "content": rendered_content,
            "truncated": truncated,
            "original_chars": original_chars,
        }


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


def _read_instruction_text_file(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _relative_posix(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    text = relative.as_posix()
    return text or "."
