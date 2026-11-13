"""Validation of the picker hints an input can carry: ``source`` and ``params``.

A starter card input, a starter card field and a question may name a
``source`` (``jira.issues``, ``github.branches``...) so the Portal offers a
picker in place of a plain text box, with ``params`` narrowing what the picker
lists. Both arrive from places nothing else validates -- a YAML file in the
agents repository, the arguments of a model tool call -- and are handed to the
Portal as they are, so the rules live here once, for the gateway's card loader
and for the ``question`` tool. The module sits in the runtime package because
the gateway imports the runtime and never the other way round.

Nothing here raises: a value that does not fit is dropped, and the input it
belonged to stays the plain text input it was before these keys existed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

# The Portal owns the list of sources; the runtime only checks the shape, so a
# source added there needs no runtime release.
_SOURCE_PATTERN = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)*")
MAX_SOURCE_CHARS = 64

# One rule for a params key and for a starter card field name.
_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,31}")

MAX_PARAMS = 8
MAX_PARAM_STRING_CHARS = 200
MAX_PARAM_LIST_ITEMS = 10
MAX_PARAM_LIST_ITEM_CHARS = 100

_DROPPED = object()


def is_sequence(value: Any) -> bool:
    """Whether the value is a list-like, which a string is not.

    YAML loaders and JSON decoders do not agree on the list class they return
    (ruamel has its own), and a string is a Sequence too, so neither
    ``isinstance(value, list)`` nor ``isinstance(value, Sequence)`` alone is
    the right question.
    """

    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def is_name(value: Any) -> bool:
    """Whether the value is a lower-case identifier of at most 32 characters."""

    return isinstance(value, str) and _NAME_PATTERN.fullmatch(value) is not None


def clean_source(value: Any) -> str:
    """The source name, or "" when the value is not one.

    The value is matched as written: a name with stray spaces or capitals is a
    mistake to drop, not something to repair into a source nobody declared.
    """

    if not isinstance(value, str) or len(value) > MAX_SOURCE_CHARS:
        return ""
    return value if _SOURCE_PATTERN.fullmatch(value) is not None else ""


def clean_params(value: Any) -> dict[str, Any]:
    """The usable entries of a params mapping; empty when there are none.

    An entry with a bad key or a bad value is dropped on its own, so one typo
    does not cost the other filters, and only the first ``MAX_PARAMS`` usable
    entries are kept.
    """

    if not isinstance(value, Mapping):
        return {}
    cleaned: dict[str, Any] = {}
    for key, raw in value.items():
        if len(cleaned) >= MAX_PARAMS:
            break
        if not is_name(key):
            continue
        item = _clean_param_value(raw)
        if item is not _DROPPED:
            cleaned[key] = item
    return cleaned


def _clean_param_value(raw: Any) -> Any:
    # A bool is an int in Python and both are allowed; a float is not.
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        return raw.strip()[:MAX_PARAM_STRING_CHARS]
    if is_sequence(raw):
        if not all(isinstance(item, str) for item in raw):
            return _DROPPED
        items = [item.strip()[:MAX_PARAM_LIST_ITEM_CHARS] for item in raw]
        return [item for item in items if item][:MAX_PARAM_LIST_ITEMS]
    return _DROPPED


__all__ = [
    "MAX_PARAMS",
    "MAX_PARAM_LIST_ITEMS",
    "MAX_PARAM_LIST_ITEM_CHARS",
    "MAX_PARAM_STRING_CHARS",
    "MAX_SOURCE_CHARS",
    "clean_params",
    "clean_source",
    "is_name",
    "is_sequence",
]
