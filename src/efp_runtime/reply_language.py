"""The member's reply language: a Portal preference rendered as one system-prompt block.

The Portal resolves each member's language (an explicit "Assistant replies
in" choice, else the UI language or the browser's) and sends it with every
proxied request as ``metadata.portal_user.reply_language`` plus
``reply_language_strict`` (true when the member chose it). The texts live in
``prompts/reply_language.json``, JSON-escaped so the repository stays ASCII
and shared verbatim with the opencode runtime, so wording changes never
touch the builder.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

REPLY_LANGUAGE_TEXTS_PATH = Path(__file__).resolve().parent / "prompts" / "reply_language.json"


@dataclass(frozen=True)
class ReplyLanguage:
    """A reply language that changes the prompt.

    ``tag`` is one of the tags in the texts file; ``strict`` is whether the
    member chose the language explicitly (always reply in it) rather than the
    Portal resolving it from the UI language (follow the member's message).
    """

    tag: str
    strict: bool

    @property
    def display_name(self) -> str:
        return str(_texts()["languages"][self.tag]["display_name"])


def resolve_reply_language(metadata: Mapping[str, Any] | None) -> ReplyLanguage | None:
    """The reply language a run should render, or None for today's prompt.

    None covers everything that must not change the prompt: no trusted
    ``portal_user`` object, a missing or unknown tag, and English without an
    explicit choice, which is what every member got before the setting
    existed. Only a JSON ``true`` makes the choice strict.
    """
    if not isinstance(metadata, Mapping):
        return None
    portal_user = metadata.get("portal_user")
    if not isinstance(portal_user, Mapping):
        return None
    tag = portal_user.get("reply_language")
    if not isinstance(tag, str):
        return None
    tag = tag.strip()
    if tag not in _texts()["languages"]:
        return None
    strict = portal_user.get("reply_language_strict") is True
    if tag == "en" and not strict:
        return None
    return ReplyLanguage(tag=tag, strict=strict)


def render_reply_language_block(reply_language: ReplyLanguage) -> str:
    """The ``Reply language:`` system block for ``reply_language``."""
    texts = _texts()
    language = texts["languages"][reply_language.tag]
    display_name = str(language["display_name"])
    mode_sentence = str(texts["mode_sentences"]["strict" if reply_language.strict else "soft"])
    return str(texts["block"]).format(
        display_name=display_name,
        language_sentence=str(language["language_sentence"]),
        mode_sentence=mode_sentence.format(display_name=display_name),
    )


@lru_cache(maxsize=1)
def _texts() -> dict[str, Any]:
    with REPLY_LANGUAGE_TEXTS_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


__all__ = [
    "REPLY_LANGUAGE_TEXTS_PATH",
    "ReplyLanguage",
    "render_reply_language_block",
    "resolve_reply_language",
]
