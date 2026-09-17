"""Per-member standing notes: what a member asked this assistant to remember.

A note is one sentence the member wants applied in every future session with
this assistant ("reply in Chinese", "PR descriptions follow my template").
Notes are kept per member id, next to the sessions on the assistant's own
storage, and rendered into the system prompt on every run. Nothing here is
extracted automatically: a note exists only because the model called the
``memory`` tool, which the instructions reserve for explicit member requests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
from typing import Any, Protocol
import uuid

from .types import utc_now_iso


SCHEMA_VERSION = 1
DEFAULT_MAX_NOTES = 50
DEFAULT_MAX_NOTE_CHARS = 300
NOTE_ID_PREFIX = "n_"

_WHITESPACE = re.compile(r"\s+")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_SAFE_NAME = 80


class MemberMemoryError(ValueError):
    """A note could not be stored or removed. The message is written for the model."""


@dataclass(frozen=True)
class MemberNote:
    note_id: str
    text: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "note_id": self.note_id,
            "text": self.text,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemberNote | None":
        note_id = data.get("note_id")
        text = data.get("text")
        if not isinstance(note_id, str) or not note_id.strip():
            return None
        if not isinstance(text, str) or not text.strip():
            return None
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")
        created = created_at if isinstance(created_at, str) and created_at else utc_now_iso()
        updated = updated_at if isinstance(updated_at, str) and updated_at else created
        return cls(note_id=note_id.strip(), text=text, created_at=created, updated_at=updated)


class MemberNotesStore(Protocol):
    """What the memory tool and the prompt builder need from a notes backend."""

    max_notes: int
    max_note_chars: int

    def list_notes(self, member_id: str) -> list[MemberNote]: ...

    def add_note(self, member_id: str, text: str) -> tuple[MemberNote, bool]: ...

    def remove_note(self, member_id: str, note_id: str) -> MemberNote | None: ...


def normalize_member_id(member_id: Any) -> str:
    if not isinstance(member_id, str) or not member_id.strip():
        raise MemberMemoryError(
            "This run carries no member identity, so notes cannot be kept or read."
        )
    return member_id.strip()


def normalize_note_text(text: Any, *, max_chars: int = DEFAULT_MAX_NOTE_CHARS) -> str:
    if not isinstance(text, str):
        raise MemberMemoryError("A note needs text.")
    normalized = _WHITESPACE.sub(" ", text).strip()
    if not normalized:
        raise MemberMemoryError("A note needs text.")
    if len(normalized) > max_chars:
        raise MemberMemoryError(
            f"The note is {len(normalized)} characters; keep it to one sentence of at most "
            f"{max_chars} characters."
        )
    return normalized


def _new_note_id(existing: set[str]) -> str:
    while True:
        candidate = NOTE_ID_PREFIX + uuid.uuid4().hex[:8]
        if candidate not in existing:
            return candidate


def _append_note(
    notes: list[MemberNote],
    text: str,
    *,
    max_notes: int,
) -> tuple[list[MemberNote], MemberNote, bool]:
    """Return the updated list, the note, and whether it was newly created."""

    folded = text.casefold()
    for note in notes:
        if note.text.casefold() == folded:
            return notes, note, False
    if len(notes) >= max_notes:
        raise MemberMemoryError(
            f"This member already has {max_notes} notes, the maximum. Forget one that no "
            "longer applies before remembering another."
        )
    now = utc_now_iso()
    note = MemberNote(
        note_id=_new_note_id({item.note_id for item in notes}),
        text=text,
        created_at=now,
        updated_at=now,
    )
    return [*notes, note], note, True


class InMemoryMemberNotesStore:
    """Process-local notes, for runtimes without a file-backed session store."""

    def __init__(
        self,
        *,
        max_notes: int = DEFAULT_MAX_NOTES,
        max_note_chars: int = DEFAULT_MAX_NOTE_CHARS,
    ) -> None:
        self.max_notes = _positive(max_notes, "max_notes")
        self.max_note_chars = _positive(max_note_chars, "max_note_chars")
        self._notes: dict[str, list[MemberNote]] = {}
        self._lock = RLock()

    def list_notes(self, member_id: str) -> list[MemberNote]:
        key = normalize_member_id(member_id)
        with self._lock:
            return list(self._notes.get(key, []))

    def add_note(self, member_id: str, text: str) -> tuple[MemberNote, bool]:
        key = normalize_member_id(member_id)
        normalized = normalize_note_text(text, max_chars=self.max_note_chars)
        with self._lock:
            notes, note, created = _append_note(
                self._notes.get(key, []),
                normalized,
                max_notes=self.max_notes,
            )
            self._notes[key] = notes
            return note, created

    def remove_note(self, member_id: str, note_id: str) -> MemberNote | None:
        key = normalize_member_id(member_id)
        with self._lock:
            notes = self._notes.get(key, [])
            for note in notes:
                if note.note_id == note_id:
                    self._notes[key] = [item for item in notes if item.note_id != note_id]
                    return note
            return None


class FileMemberNotesStore:
    """One JSON file per member under ``root``, written atomically.

    The root normally sits next to the session store (``<session root>/memory``)
    so notes persist on the same volume as the sessions they belong to.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        max_notes: int = DEFAULT_MAX_NOTES,
        max_note_chars: int = DEFAULT_MAX_NOTE_CHARS,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_notes = _positive(max_notes, "max_notes")
        self.max_note_chars = _positive(max_note_chars, "max_note_chars")
        self._lock = RLock()

    def list_notes(self, member_id: str) -> list[MemberNote]:
        key = normalize_member_id(member_id)
        with self._lock:
            return self._read(key)

    def add_note(self, member_id: str, text: str) -> tuple[MemberNote, bool]:
        key = normalize_member_id(member_id)
        normalized = normalize_note_text(text, max_chars=self.max_note_chars)
        with self._lock:
            notes, note, created = _append_note(
                self._read(key),
                normalized,
                max_notes=self.max_notes,
            )
            if created:
                self._write(key, notes)
            return note, created

    def remove_note(self, member_id: str, note_id: str) -> MemberNote | None:
        key = normalize_member_id(member_id)
        with self._lock:
            notes = self._read(key)
            removed = next((note for note in notes if note.note_id == note_id), None)
            if removed is None:
                return None
            self._write(key, [note for note in notes if note.note_id != note_id])
            return removed

    def member_path(self, member_id: str) -> Path:
        """The file that holds ``member_id``'s notes; the name is safe and stable."""

        key = normalize_member_id(member_id)
        safe = _UNSAFE_NAME.sub("-", key).strip("-.")
        if safe != key or len(safe) > _MAX_SAFE_NAME:
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
            safe = f"{safe[:40].rstrip('-.')}-{digest}" if safe else digest
        path = (self.root / f"{safe}.json").resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise MemberMemoryError("Invalid member identity.") from exc
        return path

    def _read(self, member_id: str) -> list[MemberNote]:
        path = self.member_path(member_id)
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            # A corrupt file must not stop the run; the member can re-add notes.
            return []
        raw_notes = payload.get("notes") if isinstance(payload, Mapping) else None
        if not isinstance(raw_notes, list):
            return []
        notes: list[MemberNote] = []
        seen: set[str] = set()
        for item in raw_notes:
            if not isinstance(item, Mapping):
                continue
            note = MemberNote.from_dict(item)
            if note is None or note.note_id in seen:
                continue
            seen.add(note.note_id)
            notes.append(note)
        return notes

    def _write(self, member_id: str, notes: list[MemberNote]) -> None:
        path = self.member_path(member_id)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "member_id": member_id,
            "updated_at": utc_now_iso(),
            "notes": [note.to_dict() for note in notes],
        }
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.root),
                prefix=f".{path.stem}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp_name = handle.name
                handle.write(text)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if tmp_name is not None:
                tmp_path = Path(tmp_name)
                if tmp_path.exists():
                    tmp_path.unlink()


def notes_to_payload(notes: list[MemberNote]) -> list[dict[str, str]]:
    """Newest first, the order both the prompt and the tool show notes in."""

    ordered = sorted(notes, key=lambda note: (note.created_at, note.note_id), reverse=True)
    return [note.to_dict() for note in ordered]


def render_note_line(note: Mapping[str, Any]) -> str:
    created = str(note.get("created_at") or "")
    return f"- [{note.get('note_id')}] {created[:10]}: {note.get('text')}"


def _positive(value: Any, name: str) -> int:
    number = int(value)
    if number < 1:
        raise ValueError(f"{name} must be at least 1")
    return number


__all__ = [
    "DEFAULT_MAX_NOTES",
    "DEFAULT_MAX_NOTE_CHARS",
    "FileMemberNotesStore",
    "InMemoryMemberNotesStore",
    "MemberMemoryError",
    "MemberNote",
    "MemberNotesStore",
    "normalize_member_id",
    "normalize_note_text",
    "notes_to_payload",
    "render_note_line",
]
