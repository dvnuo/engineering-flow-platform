"""Resolve conservative workspace file references in user prompts."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import stat
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional, Tuple, Union

from ..session.models import MessagePart
from ..tools.builtin.filesystem import (
    normalize_workspace_root,
    resolve_workspace_path,
    workspace_relative_path,
)
from ..types import Attachment


PromptReferenceKind = Literal[
    "file",
    "directory",
    "missing",
    "outside",
    "unsupported",
]

_TRAILING_REFERENCE_PUNCTUATION = ".,;:)"
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_TEXT_CONTROL_BYTES = {8, 9, 10, 12, 13}


@dataclass
class PromptReference:
    raw: str
    path: Optional[str]
    kind: PromptReferenceKind
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.metadata = dict(self.metadata)


@dataclass
class ResolvedPrompt:
    text: str
    parts: List[MessagePart]
    references: List[PromptReference]

    def __post_init__(self) -> None:
        self.parts = list(self.parts)
        self.references = list(self.references)


@dataclass(frozen=True)
class _ReferenceToken:
    raw: str
    path: str


def resolve_prompt_references(
    text: str,
    *,
    workspace_root: Union[str, Path],
    max_file_chars: int = 20000,
    max_directory_entries: int = 200,
) -> ResolvedPrompt:
    """Resolve ``@path`` prompt references into structured message parts.

    The original prompt text is always kept as the first part. References are
    intentionally conservative: only tokens beginning at the start of a line or
    after whitespace are considered, and URL/email-like tokens are ignored.
    """

    if max_file_chars < 0:
        raise ValueError("max_file_chars must be greater than or equal to 0")
    if max_directory_entries < 0:
        raise ValueError("max_directory_entries must be greater than or equal to 0")

    root = normalize_workspace_root(workspace_root)
    references: List[PromptReference] = []
    parts: List[MessagePart] = [MessagePart.text_part(text)]

    for token in _iter_reference_tokens(text):
        reference, part = _resolve_reference(
            token,
            workspace_root=root,
            max_file_chars=max_file_chars,
            max_directory_entries=max_directory_entries,
        )
        references.append(reference)
        if part is not None:
            parts.append(part)

    return ResolvedPrompt(text=text, parts=parts, references=references)


def _iter_reference_tokens(text: str) -> Iterator[_ReferenceToken]:
    index = 0
    length = len(text)
    while index < length:
        at_index = text.find("@", index)
        if at_index < 0:
            return
        if at_index > 0 and not text[at_index - 1].isspace():
            index = at_index + 1
            continue

        end_index = at_index + 1
        while end_index < length and not text[end_index].isspace():
            end_index += 1

        raw = text[at_index:end_index].rstrip(_TRAILING_REFERENCE_PUNCTUATION)
        path_value = raw[1:]
        index = end_index
        if not path_value or _should_ignore_token(path_value):
            continue
        yield _ReferenceToken(raw=raw, path=path_value)


def _should_ignore_token(path_value: str) -> bool:
    if _URL_SCHEME_RE.match(path_value):
        return True
    return "@" in path_value


def _resolve_reference(
    token: _ReferenceToken,
    *,
    workspace_root: Path,
    max_file_chars: int,
    max_directory_entries: int,
) -> Tuple[PromptReference, Optional[MessagePart]]:
    try:
        resolved_path = resolve_workspace_path(workspace_root, token.path)
    except ValueError:
        return _error_reference(
            token,
            kind="outside",
            path=token.path,
            reason="outside_workspace",
            text="Prompt reference {0} escapes the workspace root.".format(token.raw),
        )

    relative_path = workspace_relative_path(workspace_root, resolved_path)
    if not resolved_path.exists():
        return _error_reference(
            token,
            kind="missing",
            path=relative_path,
            reason="missing",
            text="Prompt reference {0} was not found in the workspace.".format(token.raw),
        )
    if resolved_path.is_file():
        return _file_reference(
            token,
            path=resolved_path,
            relative_path=relative_path,
            max_file_chars=max_file_chars,
        )
    if resolved_path.is_dir():
        return _directory_reference(
            token,
            workspace_root=workspace_root,
            path=resolved_path,
            relative_path=relative_path,
            max_directory_entries=max_directory_entries,
        )
    return _error_reference(
        token,
        kind="unsupported",
        path=relative_path,
        reason="unsupported_path_type",
        text="Prompt reference {0} is not a file or directory.".format(token.raw),
    )


def _file_reference(
    token: _ReferenceToken,
    *,
    path: Path,
    relative_path: str,
    max_file_chars: int,
) -> Tuple[PromptReference, MessagePart]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return _error_reference(
            token,
            kind="unsupported",
            path=relative_path,
            reason="read_failed",
            text="Prompt reference {0} could not be read: {1}".format(
                token.raw,
                str(exc) or exc.__class__.__name__,
            ),
        )

    metadata: Dict[str, Any] = {
        "kind": "prompt_reference",
        "raw": token.raw,
        "path": relative_path,
        "bytes": len(data),
        "truncated": False,
        "original_chars": 0,
    }
    if _looks_binary(data):
        metadata["content_type"] = "binary"
    else:
        content = data.decode("utf-8", errors="replace")
        metadata.update(
            {
                "content_type": "text",
                "encoding": "utf-8",
                "content": content[:max_file_chars],
                "truncated": len(content) > max_file_chars,
                "original_chars": len(content),
            }
        )

    attachment = Attachment(
        mime_type="text/plain",
        filename=path.name,
        text_ref=relative_path,
        metadata=metadata,
    )
    part = MessagePart.attachment_part(
        attachment,
        metadata=_part_metadata(token, relative_path, "file"),
    )
    return (
        PromptReference(
            raw=token.raw,
            path=relative_path,
            kind="file",
            metadata=metadata,
        ),
        part,
    )


def _directory_reference(
    token: _ReferenceToken,
    *,
    workspace_root: Path,
    path: Path,
    relative_path: str,
    max_directory_entries: int,
) -> Tuple[PromptReference, MessagePart]:
    try:
        all_entries = [
            _directory_entry(workspace_root, entry)
            for entry in sorted(path.iterdir(), key=_sort_key)
        ]
    except OSError as exc:
        return _error_reference(
            token,
            kind="unsupported",
            path=relative_path,
            reason="read_failed",
            text="Prompt reference {0} could not be listed: {1}".format(
                token.raw,
                str(exc) or exc.__class__.__name__,
            ),
        )

    entries = all_entries[:max_directory_entries]
    truncated = len(all_entries) > len(entries)
    metadata: Dict[str, Any] = {
        "kind": "prompt_reference",
        "raw": token.raw,
        "path": relative_path,
        "content_type": "directory_listing",
        "entry_count": len(all_entries),
        "included_entry_count": len(entries),
        "entries": entries,
        "truncated": truncated,
        "content": _format_directory_listing(relative_path, entries, truncated),
    }
    attachment = Attachment(
        mime_type="text/plain",
        filename=relative_path,
        text_ref=relative_path,
        metadata=metadata,
    )
    part = MessagePart.attachment_part(
        attachment,
        metadata=_part_metadata(token, relative_path, "directory"),
    )
    return (
        PromptReference(
            raw=token.raw,
            path=relative_path,
            kind="directory",
            metadata=metadata,
        ),
        part,
    )


def _error_reference(
    token: _ReferenceToken,
    *,
    kind: PromptReferenceKind,
    path: str,
    reason: str,
    text: str,
) -> Tuple[PromptReference, MessagePart]:
    metadata = {
        "kind": "prompt_reference_error",
        "raw": token.raw,
        "path": path,
        "reason": reason,
    }
    return (
        PromptReference(raw=token.raw, path=path, kind=kind, metadata=metadata),
        MessagePart.error_part(text, metadata=metadata),
    )


def _part_metadata(
    token: _ReferenceToken,
    relative_path: str,
    reference_kind: PromptReferenceKind,
) -> Dict[str, Any]:
    return {
        "kind": "prompt_reference",
        "raw": token.raw,
        "path": relative_path,
        "reference_kind": reference_kind,
    }


def _directory_entry(workspace_root: Path, path: Path) -> Dict[str, Any]:
    file_stat = path.lstat()
    mode = file_stat.st_mode
    if stat.S_ISDIR(mode):
        entry_type = "directory"
    elif stat.S_ISREG(mode):
        entry_type = "file"
    elif stat.S_ISLNK(mode):
        entry_type = "symlink"
    else:
        entry_type = "other"
    return {
        "name": path.name,
        "path": workspace_relative_path(workspace_root, path),
        "type": entry_type,
        "size": file_stat.st_size if entry_type == "file" else None,
    }


def _format_directory_listing(
    relative_path: str,
    entries: List[Dict[str, Any]],
    truncated: bool,
) -> str:
    lines = ["{0}/".format(relative_path.rstrip("/") or ".")]
    for entry in entries:
        size = entry.get("size")
        suffix = "" if size is None else " ({0} bytes)".format(size)
        lines.append("{0}\t{1}{2}".format(entry["type"], entry["path"], suffix))
    if truncated:
        lines.append("... directory listing truncated")
    return "\n".join(lines)


def _looks_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    sample = data[:1024]
    if not sample:
        return False
    control_bytes = sum(
        1
        for value in sample
        if value < 32 and value not in _TEXT_CONTROL_BYTES
    )
    return control_bytes / len(sample) > 0.30


def _sort_key(path: Path) -> Tuple[str, str]:
    return (path.name.casefold(), path.name)


__all__ = ["PromptReference", "ResolvedPrompt", "resolve_prompt_references"]
