"""The member's reply language: selection from trusted metadata and the rendered block."""

from __future__ import annotations

from pathlib import Path

import pytest

import efp_runtime.reply_language as reply_language_module

from efp_runtime.reply_language import (
    REPLY_LANGUAGE_TEXTS_PATH,
    ReplyLanguage,
    render_reply_language_block,
    resolve_reply_language,
)


ROOT = Path(__file__).resolve().parents[2]

DISPLAY_NAMES = {
    "en": "English",
    "zh-Hans": "Simplified Chinese",
    "zh-Hant-HK": "Traditional Chinese (Hong Kong)",
}
LANGUAGE_SENTENCES = {
    "en": "Write your prose in English.",
    "zh-Hans": "Write your prose in Simplified Chinese as used in mainland China (\u7b80\u4f53\u4e2d\u6587).",
    "zh-Hant-HK": (
        "Write your prose in Traditional Chinese characters using Standard Written Chinese with Hong Kong vocabulary "
        "(\u7e41\u9ad4\u4e2d\u6587\uff0c\u9999\u6e2f\u7528\u8a9e\uff1a\u8edf\u4ef6\u3001\u4f3a\u670d\u5668\u3001\u7db2\u7d61\u3001\u767b\u5165\u3001\u8a2d\u5b9a\u3001\u9810\u8a2d), "
        "not vernacular Cantonese and not Taiwan vocabulary."
    ),
}
SOFT_SENTENCE = (
    "This is the member's default, taken from their Portal language rather than chosen for replies. "
    "When the member clearly writes to you in another language, or asks for a particular reply language "
    "in this chat or in a saved note, reply in that language instead. "
    "Messages the Portal sends on the member's behalf, such as \"Continue\", a request to fix a diagram "
    "or a notice from a panel, do not change the language."
)
FIXED_LINES = [
    "Keep code, commands, file paths, identifiers, error text, product names and established technical terms "
    "exactly as they are, usually in English.",
    "Everything you write into a repository or an external system stays in English unless the user asks otherwise: "
    "code and comments, commit messages, pull request titles and descriptions, documentation, tickets and wiki pages.",
    "This setting comes from the member's Portal language preference.",
]


def _metadata(tag, strict=None):
    portal_user = {"id": "77", "username": "runtime-user", "display_name": "Runtime User"}
    if tag is not None:
        portal_user["reply_language"] = tag
    if strict is not None:
        portal_user["reply_language_strict"] = strict
    return {"portal_user": portal_user}


# --- selection ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "strict", "expected"),
    [
        (None, None, None),
        (None, True, None),
        ("", True, None),
        ("fr", True, None),
        ("zh", True, None),
        ("ZH-HANS", True, None),
        (123, True, None),
        (["zh-Hans"], True, None),
        ("en", None, None),
        ("en", False, None),
        ("en", True, ReplyLanguage("en", True)),
        ("zh-Hans", None, ReplyLanguage("zh-Hans", False)),
        ("zh-Hans", False, ReplyLanguage("zh-Hans", False)),
        ("zh-Hans", True, ReplyLanguage("zh-Hans", True)),
        ("zh-Hant-HK", None, ReplyLanguage("zh-Hant-HK", False)),
        ("zh-Hant-HK", False, ReplyLanguage("zh-Hant-HK", False)),
        ("zh-Hant-HK", True, ReplyLanguage("zh-Hant-HK", True)),
    ],
    ids=[
        "absent",
        "absent but strict",
        "empty",
        "unknown",
        "bare zh",
        "wrong case",
        "not a string",
        "a list",
        "en",
        "en soft",
        "en strict",
        "zh-Hans",
        "zh-Hans soft",
        "zh-Hans strict",
        "zh-Hant-HK",
        "zh-Hant-HK soft",
        "zh-Hant-HK strict",
    ],
)
def test_resolve_reply_language_selection_table(tag, strict, expected):
    assert resolve_reply_language(_metadata(tag, strict)) == expected


def test_resolve_reply_language_without_a_portal_user_object():
    assert resolve_reply_language(None) is None
    assert resolve_reply_language({}) is None
    assert resolve_reply_language("zh-Hans") is None
    # Only the structured object counts, never a top-level or header-like field.
    assert resolve_reply_language({"reply_language": "zh-Hans", "reply_language_strict": True}) is None
    assert resolve_reply_language({"portal_user": None}) is None
    assert resolve_reply_language({"portal_user": "zh-Hans"}) is None
    assert resolve_reply_language({"portal_user": ["zh-Hans"]}) is None
    assert resolve_reply_language({"portal_user": 7}) is None


def test_resolve_reply_language_does_not_need_the_identity_fields():
    assert resolve_reply_language({"portal_user": {"reply_language": "zh-Hans"}}) == ReplyLanguage("zh-Hans", False)


@pytest.mark.parametrize("value", ["true", "True", "yes", 1, 0, None, [True], {"strict": True}])
def test_only_a_json_true_makes_the_choice_strict(value):
    assert resolve_reply_language(_metadata("zh-Hans", value)) == ReplyLanguage("zh-Hans", False)
    assert resolve_reply_language(_metadata("en", value)) is None


def test_whitespace_around_the_tag_is_ignored():
    assert resolve_reply_language(_metadata(" zh-Hant-HK\n", True)) == ReplyLanguage("zh-Hant-HK", True)


# --- the block ----------------------------------------------------------------


@pytest.mark.parametrize("strict", [False, True], ids=["soft", "strict"])
@pytest.mark.parametrize("tag", ["en", "zh-Hans", "zh-Hant-HK"])
def test_render_reply_language_block_text(tag, strict):
    display_name = DISPLAY_NAMES[tag]
    mode_sentence = (
        f"Always reply in {display_name}, even when the user writes in another language, "
        "unless the user explicitly asks you to switch. "
        "The member chose this in the Portal, so it wins over earlier notes about reply language."
        if strict
        else SOFT_SENTENCE
    )

    block = render_reply_language_block(ReplyLanguage(tag, strict))

    assert block.splitlines() == [
        f"Reply language: {display_name}.",
        LANGUAGE_SENTENCES[tag],
        mode_sentence,
        *FIXED_LINES,
    ]
    assert not block.endswith("\n")
    assert "{" not in block and "}" not in block


def test_render_reply_language_block_verbatim_for_the_simplified_chinese_default():
    assert render_reply_language_block(ReplyLanguage("zh-Hans", False)) == (
        "Reply language: Simplified Chinese.\n"
        "Write your prose in Simplified Chinese as used in mainland China (\u7b80\u4f53\u4e2d\u6587).\n"
        f"{SOFT_SENTENCE}\n"
        "Keep code, commands, file paths, identifiers, error text, product names and established technical terms "
        "exactly as they are, usually in English.\n"
        "Everything you write into a repository or an external system stays in English unless the user asks otherwise: "
        "code and comments, commit messages, pull request titles and descriptions, documentation, tickets and wiki pages.\n"
        "This setting comes from the member's Portal language preference."
    )


def test_only_an_explicit_choice_wins_over_a_saved_note():
    soft = render_reply_language_block(ReplyLanguage("zh-Hans", False))
    strict = render_reply_language_block(ReplyLanguage("zh-Hans", True))
    # A language derived from the browser or the UI is only a default: the
    # member's own request or saved note still decides.
    assert "wins over earlier notes" not in soft
    assert "or asks for a particular reply language in this chat or in a saved note" in soft
    assert 'such as "Continue"' in soft
    assert "wins over earlier notes about reply language" in strict


def test_display_name_comes_from_the_texts_file():
    assert ReplyLanguage("en", True).display_name == "English"
    assert ReplyLanguage("zh-Hans", False).display_name == "Simplified Chinese"
    assert ReplyLanguage("zh-Hant-HK", True).display_name == "Traditional Chinese (Hong Kong)"


def test_texts_file_ships_inside_the_package_and_stays_ascii():
    # The image copies the whole tree, so a file inside the package needs no
    # packaging rule; the repository keeps CJK as JSON escapes.
    assert REPLY_LANGUAGE_TEXTS_PATH == ROOT / "src" / "efp_runtime" / "prompts" / "reply_language.json"
    raw = REPLY_LANGUAGE_TEXTS_PATH.read_bytes()
    assert raw.isascii()
    assert b"\\u7b80\\u4f53\\u4e2d\\u6587" in raw


def test_a_missing_texts_file_keeps_todays_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(reply_language_module, "REPLY_LANGUAGE_TEXTS_PATH", tmp_path / "missing.json")
    reply_language_module._texts.cache_clear()
    try:
        metadata = {"portal_user": {"id": "77", "reply_language": "zh-Hans", "reply_language_strict": True}}
        assert reply_language_module.resolve_reply_language(metadata) is None
    finally:
        reply_language_module._texts.cache_clear()
