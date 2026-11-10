"""Per-run, in-memory journal of a chat run's runtime events.

A viewer that joins a run late -- a refreshed page, a second window, a tab
that was asleep -- asks the event socket for everything after the last
sequence number it applied and gets exactly that. The bus history could only
offer a fixed-size tail of what every run emitted last, so a late viewer saw
the end of the run's steps, or none of them.

The journal belongs to the run's registry record. It is never written to
disk, it is capped, and it goes with the record or a few minutes after the
run ended, whichever comes first.
"""

from __future__ import annotations

import json
import time
from array import array
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

DEFAULT_MAX_RECORDS = 2000
DEFAULT_MAX_BYTES = 2 * 1024 * 1024

# One merged record stops growing here and the part's next delta starts
# another, so no single socket message carries more than this much text.
# Portal relays the socket with a 1 MiB limit per message.
MAX_MERGED_RECORD_BYTES = 128 * 1024

# The pieces of one merged record are joined once this many have piled up, so
# serving the record never has to join thousands of them.
_CHUNK_JOIN_AT = 256

# The keys a delta event repeats its text under.
_DELTA_TEXT_KEYS = frozenset(
    {
        "delta",
        "text_delta",
        "content_delta",
        "message_delta",
        "content",
        "text",
        "input_delta",
        "arguments_delta",
    }
)

MergeKey = Tuple[str, str, str, str]


def _serialized_size(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def _delta_of(event: Dict[str, Any]) -> Tuple[Optional[MergeKey], str]:
    """The merge key and the text of a delta event; ``(None, "")`` for any other."""
    event_type = str(event.get("type") or event.get("event_type") or "")
    if not event_type.endswith(".delta"):
        return None, ""
    data = event.get("data")
    if not isinstance(data, dict):
        return None, ""
    delta = data.get("delta")
    if not isinstance(delta, str) or not delta:
        return None, ""
    # Every identifier has to agree, not just the first one present: two tool
    # calls streaming their arguments in turn share a message id.
    key = (
        event_type,
        str(data.get("part_id") or event.get("part_id") or ""),
        str(data.get("tool_call_id") or data.get("call_id") or ""),
        str(data.get("message_id") or event.get("message_id") or ""),
    )
    return key, delta


class _JournalRecord:
    """One event, or a run of consecutive deltas of one part kept as one."""

    __slots__ = ("first_seq", "last_seq", "event", "ts", "size", "merge_key", "chunks", "ends")

    def __init__(
        self,
        seq: int,
        event: Dict[str, Any],
        ts: float,
        size: int,
        merge_key: Optional[MergeKey],
        delta: str,
    ) -> None:
        self.first_seq = seq
        self.last_seq = seq
        self.event = event
        self.ts = ts
        self.size = size
        self.merge_key = merge_key
        # Only a delta record has text. ``chunks`` joined is all of it, and
        # ``ends[i]`` is its length after the delta numbered ``first_seq + i``:
        # where a viewer that already has that delta picks the text up.
        self.chunks: Optional[List[str]] = [delta] if merge_key is not None else None
        self.ends: Optional[array] = array("I", [len(delta)]) if merge_key is not None else None

    def extend(self, seq: int, event: Dict[str, Any], ts: float, delta: str) -> None:
        self.chunks.append(delta)
        if len(self.chunks) >= _CHUNK_JOIN_AT:
            self.chunks = ["".join(self.chunks)]
        self.ends.append(self.ends[-1] + len(delta))
        self.last_seq = seq
        # The newest delta stands for the record: it is what a viewer one
        # delta behind is sent, and the frame of what the others are sent.
        self.event = event
        self.ts = ts

    def text_after(self, after_seq: int) -> str:
        """The joined text of the deltas numbered above ``after_seq``."""
        if len(self.chunks) > 1:
            self.chunks = ["".join(self.chunks)]
        text = self.chunks[0]
        seen = after_seq - self.first_seq
        return text[self.ends[seen]:] if seen >= 0 else text

    def served(self, after_seq: int) -> Dict[str, Any]:
        """The record as a viewer that has everything up to ``after_seq`` needs it."""
        event = dict(self.event)
        event["run_seq"] = self.last_seq
        if self.merge_key is None:
            return event
        first_unseen = max(self.first_seq, after_seq + 1)
        if first_unseen >= self.last_seq:
            # Only the newest delta is missing: the event as it was sent.
            return event
        # Several deltas in one event: say where the stretch starts, so the
        # viewer can tell that it follows on from what it has.
        event["run_seq_from"] = first_unseen
        if event.get("id"):
            # To the viewer this is a new event, not the newest delta again.
            # Event ids are content hashes, so the delta's own id may be one
            # the viewer has already met -- the same words earlier in the same
            # part -- and it skips an id it has met.
            event["id"] = f"{event['id']}@{first_unseen}-{self.last_seq}"
        newest = self.event["data"]["delta"]
        text = self.text_after(after_seq)
        for field in ("data", "properties"):
            payload = event.get(field)
            if not isinstance(payload, dict) or "delta" not in payload:
                continue
            # The joined text goes under "delta" alone. The event's other
            # spellings of its own delta are left out rather than repeated.
            payload = {
                key: value
                for key, value in payload.items()
                if key not in _DELTA_TEXT_KEYS or value != newest
            }
            payload["delta"] = text
            event[field] = payload
        return event


class ChatRunJournal:
    """A run's events in order, each under the sequence number the run gave it.

    Consecutive deltas of one part (answer text, tool arguments) are kept as
    a single record, so a long answer costs a few entries rather than
    thousands; a viewer that already has the first deltas of a record is
    served only the rest of its text. When a cap is reached the oldest
    records go, and a viewer that needed them can be told so
    (``can_serve_after``).
    """

    def __init__(
        self,
        *,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_merged_record_bytes: int = MAX_MERGED_RECORD_BYTES,
    ) -> None:
        self._records: Deque[_JournalRecord] = deque()
        self._max_records = max(1, int(max_records))
        self._max_bytes = max(1, int(max_bytes))
        self._max_merged_record_bytes = max(1, int(max_merged_record_bytes))
        self._bytes = 0
        self._last_seq = 0

    @property
    def last_seq(self) -> int:
        return self._last_seq

    @property
    def first_seq(self) -> int:
        """The lowest sequence number still held (the next one to come when empty)."""
        return self._records[0].first_seq if self._records else self._last_seq + 1

    @property
    def truncated(self) -> bool:
        """Whether the start of the run has been dropped to stay under a cap."""
        return self.first_seq > 1

    def can_serve_after(self, after_seq: int) -> bool:
        """Whether every event above ``after_seq`` is still here."""
        return self.first_seq <= max(0, int(after_seq or 0)) + 1

    def append(self, seq: int, event: Dict[str, Any]) -> None:
        """Add the run's next event. ``seq`` has to be above the last one."""
        seq = int(seq)
        if seq <= self._last_seq:
            return
        ts = time.time()
        merge_key, delta = _delta_of(event)
        last = self._records[-1] if self._records else None
        added = _serialized_size(delta) if merge_key is not None else 0
        if (
            merge_key is not None
            and last is not None
            and last.merge_key == merge_key
            and seq == last.last_seq + 1
            and last.size + added <= self._max_merged_record_bytes
        ):
            last.extend(seq, event, ts, delta)
            last.size += added
        else:
            added = _serialized_size(event)
            self._records.append(_JournalRecord(seq, event, ts, added, merge_key, delta))
        self._bytes += added
        self._last_seq = seq
        # The record just written always stays.
        while len(self._records) > 1 and (
            len(self._records) > self._max_records or self._bytes > self._max_bytes
        ):
            self._bytes -= self._records.popleft().size

    def records_after(self, after_seq: int) -> List[Tuple[Dict[str, Any], float]]:
        """``(event, timestamp)`` for everything above ``after_seq``, oldest first.

        Each event is a copy stamped with ``run_seq``, the last sequence number
        it covers. One that covers several deltas also says where it starts
        (``run_seq_from``) and carries their joined text under ``delta``.
        """
        after = max(0, int(after_seq or 0))
        served: List[Tuple[Dict[str, Any], float]] = []
        for record in reversed(self._records):
            if record.last_seq <= after:
                break
            served.append((record.served(after), record.ts))
        served.reverse()
        return served
